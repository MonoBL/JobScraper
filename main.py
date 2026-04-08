#!/usr/bin/env python3
"""
Daily Job Scraper & Ranking Bot
Scrapes Web3/Crypto job boards and ranks them based on Nuno's profile.
Uses Playwright for JavaScript-rendered content.
"""

# Bot version - update this when making significant changes
BOT_VERSION = "v3.0"

import os
import re
import sys
import json
import signal
import logging
import asyncio
import random
import argparse
from datetime import datetime, timedelta
from typing import List, Dict, Optional, Set, Tuple
from dataclasses import dataclass, asdict
from enum import Enum
from urllib.parse import urlparse, urlunparse, parse_qs

from playwright.async_api import async_playwright, Browser, Page, TimeoutError as PlaywrightTimeoutError
from bs4 import BeautifulSoup
import requests
import schedule
import time
from pydantic import BaseModel, HttpUrl

# Try to load .env file if python-dotenv is available
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass  # python-dotenv not installed, will use environment variables only

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('job_scraper.log'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)


class JobPriority(Enum):
    """Job priority levels"""
    PERFECT_MATCH = 1
    GOOD_MATCH = 2
    WEAK_MATCH = 3
    BLACKLISTED = 0


@dataclass
class Job:
    """Job listing data structure"""
    title: str
    company: str
    url: str
    description: str
    source: str
    priority: JobPriority
    priority_reason: str
    posted_date: Optional[str] = None
    category: str = "crypto"  # "crypto" | "cruise" | "general" for Discord section separation

    def to_dict(self):
        return {
            **asdict(self),
            'priority': self.priority.name,
            'priority_value': self.priority.value
        }


def _job_category(job: Job) -> str:
    """Return job category for grouping (crypto vs cruise)."""
    return getattr(job, 'category', 'crypto') or 'crypto'


class JobRanker:
    """Ranks jobs based on Nuno's profile"""
    
    # Perfect Match criteria - Core role titles (will match with any seniority prefix)
    PERFECT_TITLES = [
        "devops", "devops engineer", "sysadmin", "system administrator", "systems administrator",
        "it systems administrator", "it system administrator", "it administrator",
        "l2 support", "level 2 support", "infrastructure engineer",
        "node operator", "node operations", "site reliability engineer",
        "sre", "sre engineer", "platform engineer", "cloud engineer",
        "linux engineer", "linux administrator",
        # Automation roles
        "automation engineer", "qa automation engineer", "test automation engineer",
        "infrastructure automation", "automation architect",
        # Product roles (technical)
        "product engineer", "product operations engineer",
    ]
    
    # Seniority prefixes to ignore when matching titles
    SENIORITY_PREFIXES = ["junior", "mid", "mid-level", "senior", "sr", "lead", "staff", "principal", "chief"]
    PERFECT_KEYWORDS = {
        'linux': ['linux', 'ubuntu', 'debian', 'centos'],
        'scripting': ['python', 'bash', 'shell scripting', 'shell script'],
        'infrastructure': ['kubernetes', 'docker', 'terraform', 'ansible', 'ci/cd', 'infrastructure', 'cloud', 'aws', 'gcp', 'azure'],
        'automation': ['automation', 'selenium', 'playwright', 'cypress', 'jenkins', 'github actions', 'gitlab ci'],
    }
    
    # Good Match criteria
    GOOD_TITLES = [
        "it support", "technical support", "datacenter technician",
        "it operations", "operations engineer", "support engineer",
        "customer support engineer", "technical support engineer",
        "solutions architect", "systems architect", "technical architect",
        "infrastructure architect", "cloud architect",
        # Automation / Product roles
        "automation specialist", "automation developer", "automation tester",
        "product manager", "technical product manager", "product owner",
        "product operations", "release engineer", "build engineer",
    ]
    GOOD_KEYWORDS = [
        "hardware", "repair", "network", "networking", "tickets",
        "on-site", "onsite", "equipment", "server maintenance",
        "troubleshooting", "monitoring", "alerting", "incident response"
    ]
    
    # Blacklist - filter out non-IT roles more strictly
    BLACKLIST_TITLES = [
        "senior solidity developer", "marketing manager", "sales manager",
        "hr manager", "human resources manager", "legal counsel", "lawyer", "attorney",
        "cfo", "cto", "founder", "co-founder",
        "ui/ux designer", "content writer", "copywriter", "community manager",
        "social media manager", "influencer", "accountant", "finance manager",
        "affiliate manager", "business development", "bd manager", "legal admin",
        "senior associate", "analyst", "client insights", "sales analytics",
        "business operations", "strategy manager",
        "internal audit", "professional practices", "compliance", "regulatory"
    ]
    BLACKLIST_KEYWORDS = [
        "senior solidity", "marketing manager", "sales manager",
        "hr manager", "legal counsel", "10+ years experience", "15+ years",
        "phd required", "masters required", "bachelor's degree required",
        "affiliate", "business development", "bd", "legal", "compliance",
        "audit", "accounting", "finance", "analyst", "sales analytics",
        "client insights", "strategy"
    ]
    
    # Additional filter: filter out clearly non-IT patterns
    IRRELEVANT_PATTERNS = [
        r'\b(chief|founder|co-founder|cto|ceo|cfo)\s+\w+',
        r'\b(15|20)\+?\s*years?\s+experience',
        r'\b(phd|masters?)\s+required',
        r'\b(marketing|sales|legal|finance|accounting|compliance|audit|regulatory)\s+\w+',
        r'\b(affiliate|business development|bd)\s+\w+',
        r'\b(senior associate|analyst|insights|analytics)\s+\w+',
    ]

    @staticmethod
    def normalize_text(text: str) -> str:
        """Normalize text for matching"""
        return text.lower().strip()

    @staticmethod
    def contains_keywords(text: str, keywords: List[str]) -> bool:
        """Check if text contains any of the keywords"""
        normalized = JobRanker.normalize_text(text)
        return any(keyword.lower() in normalized for keyword in keywords)
    
    @staticmethod
    def strip_seniority(title: str) -> str:
        """Remove seniority prefixes from job title for better matching"""
        title_lower = JobRanker.normalize_text(title)
        for prefix in JobRanker.SENIORITY_PREFIXES:
            # Remove prefix if it's at the start followed by a space
            if title_lower.startswith(prefix + " "):
                title_lower = title_lower[len(prefix) + 1:].strip()
                break
        return title_lower

    @staticmethod
    def rank_job(title: str, description: str) -> tuple[JobPriority, str]:
        """
        Rank a job based on title and description.
        Returns (priority, reason)
        """
        title_lower = JobRanker.normalize_text(title)
        desc_lower = JobRanker.normalize_text(description)
        combined = f"{title_lower} {desc_lower}"

        # Check blacklist patterns first (regex-based)
        for pattern in JobRanker.IRRELEVANT_PATTERNS:
            if re.search(pattern, combined, re.IGNORECASE):
                return JobPriority.BLACKLISTED, f"Matches irrelevant pattern: {pattern}"

        # Check blacklist titles/keywords
        if JobRanker.contains_keywords(combined, JobRanker.BLACKLIST_TITLES):
            return JobPriority.BLACKLISTED, "Contains blacklisted title/keyword"
        
        if JobRanker.contains_keywords(combined, JobRanker.BLACKLIST_KEYWORDS):
            return JobPriority.BLACKLISTED, "Contains blacklisted keyword"

        # Check Perfect Match (need title + at least 1 keyword, OR just a strong IT title)
        # Strip seniority prefixes for better matching (e.g., "Staff DevOps Engineer" -> "DevOps Engineer")
        title_stripped = JobRanker.strip_seniority(title_lower)
        has_perfect_title = JobRanker.contains_keywords(title_stripped, JobRanker.PERFECT_TITLES)
        has_linux = JobRanker.contains_keywords(combined, JobRanker.PERFECT_KEYWORDS['linux'])
        has_scripting = JobRanker.contains_keywords(combined, JobRanker.PERFECT_KEYWORDS['scripting'])
        has_infra = JobRanker.contains_keywords(combined, JobRanker.PERFECT_KEYWORDS['infrastructure'])
        has_automation = JobRanker.contains_keywords(combined, JobRanker.PERFECT_KEYWORDS['automation'])

        keyword_count = sum([has_linux, has_scripting, has_infra, has_automation])
        
        # Perfect match: Strong IT title (like "IT Systems Administrator", "Staff DevOps Engineer") is enough
        # OR title + technical keywords
        if has_perfect_title:
            if keyword_count >= 1:
                return JobPriority.PERFECT_MATCH, f"Perfect match: Title + {keyword_count} technical keyword(s)"
            # Strong IT/DevOps/SRE/Platform/Automation titles are perfect even without keywords
            strong_titles = ['system administrator', 'systems administrator', 'sysadmin', 'it administrator',
                           'devops', 'sre', 'platform engineer', 'cloud engineer', 'infrastructure engineer',
                           'automation engineer', 'product engineer']
            if any(term in title_stripped for term in strong_titles):
                return JobPriority.PERFECT_MATCH, "Perfect match: Strong IT/DevOps/SRE/Platform title"

        # Check Good Match (more lenient: title OR keywords)
        has_good_title = JobRanker.contains_keywords(title_lower, JobRanker.GOOD_TITLES)
        has_good_keywords = JobRanker.contains_keywords(combined, JobRanker.GOOD_KEYWORDS)

        if has_good_title and has_good_keywords:
            return JobPriority.GOOD_MATCH, "Good match: IT Support title + technical keywords"
        
        if has_good_title or has_good_keywords:
            return JobPriority.GOOD_MATCH, "Good match: IT Support/Hardware/Network keywords"
        
        if keyword_count >= 1:
            return JobPriority.GOOD_MATCH, f"Good match: Technical keywords found ({keyword_count})"

        # Weak Match: Only include jobs with clear technical/IT relevance
        # Must have at least one strong technical keyword
        # Exclude creative/art roles that aren't IT-related
        technical_keywords = ['support', 'operations', 'infrastructure', 'linux', 'python', 'devops',
                             'engineer', 'developer', 'admin', 'sysadmin', 'it',
                             'network', 'server', 'cloud', 'kubernetes', 'docker', 'monitoring',
                             'systems', 'platform', 'backend', 'frontend', 'sre', 'sre engineer',
                             'site reliability', 'infrastructure engineer', 'technical support',
                             'automation', 'product engineer', 'product manager', 'ci/cd', 'pipeline']
        
        # Exclude creative/art roles from technical keywords
        creative_roles = ['artist', 'designer', 'illustrator', 'animator', 'creative', 'ui/ux designer']
        has_creative_role = any(role in title_lower for role in creative_roles)
        
        has_technical = any(kw in combined for kw in technical_keywords)
        
        # Only weak match if it's technical AND not a creative role
        if has_technical and not has_creative_role:
            return JobPriority.WEAK_MATCH, "Weak match: Some technical relevance"
        
        # Only show crypto/web3 jobs if they have some technical aspect
        crypto_keywords = ['crypto', 'blockchain', 'web3', 'defi', 'bitcoin', 'ethereum', 'nft']
        has_crypto = any(kw in combined for kw in crypto_keywords)
        
        # Only show crypto jobs if they also mention technical terms
        if has_crypto and (has_technical or 'engineer' in combined or 'developer' in combined or 'technical' in combined):
            return JobPriority.WEAK_MATCH, "Weak match: Crypto/Web3 with technical aspects"

        # Blacklist everything else - too generic or not IT-related
        return JobPriority.BLACKLISTED, "Not IT-related - too generic or irrelevant"


class CruiseJobRanker:
    """Ranks cruise/maritime IT jobs. Used only for cruise category scrapers."""
    PERFECT_TITLES = [
        "it officer", "it systems manager", "systems manager", "it manager",
        "senior it officer", "staff it", "it administrator", "network engineer",
        "electro-technical", "eto ", "2nd eto", "3rd eto", "it support specialist"
    ]
    GOOD_TITLES = [
        "it assistant", "assistant it", "it support", "technical support",
        "it administrator", "jr it", "junior it", "sr it assistant"
    ]
    IT_KEYWORDS = [
        "it ", " information technology", "computer", "network", "systems",
        "software", "hardware", "technical support", "electro-technical", "eto"
    ]

    @staticmethod
    def rank_job(title: str, description: str) -> tuple[JobPriority, str]:
        combined = (title + " " + description).lower()
        if any(t in combined for t in CruiseJobRanker.PERFECT_TITLES):
            return JobPriority.PERFECT_MATCH, "Cruise IT: strong match"
        if any(t in combined for t in CruiseJobRanker.GOOD_TITLES):
            return JobPriority.GOOD_MATCH, "Cruise IT: good match"
        if any(kw in combined for kw in CruiseJobRanker.IT_KEYWORDS):
            return JobPriority.WEAK_MATCH, "Cruise IT: weak match"
        return JobPriority.BLACKLISTED, "Not IT-related"


_USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:125.0) Gecko/20100101 Firefox/125.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 14.4; rv:125.0) Gecko/20100101 Firefox/125.0",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36 Edg/123.0.0.0",
]


class PlaywrightBrowserManager:
    """Manages Playwright browser instance"""

    _browser: Optional[Browser] = None
    _playwright = None

    @classmethod
    async def get_browser(cls) -> Browser:
        """Get or create browser instance"""
        if cls._browser is None:
            cls._playwright = await async_playwright().start()
            cls._browser = await cls._playwright.chromium.launch(
                headless=True,
                args=[
                    '--no-sandbox',
                    '--disable-setuid-sandbox',
                    '--disable-dev-shm-usage',
                    '--disable-accelerated-2d-canvas',
                    '--disable-gpu',
                    '--disable-blink-features=AutomationControlled',
                    '--disable-features=IsolateOrigins,site-per-process',
                ]
            )
        return cls._browser

    @classmethod
    async def close_browser(cls):
        """Close browser instance"""
        if cls._browser:
            await cls._browser.close()
            cls._browser = None
        if cls._playwright:
            await cls._playwright.stop()
            cls._playwright = None

    @classmethod
    async def create_page(cls) -> Page:
        """Create a new page with stealth settings and a random User-Agent"""
        browser = await cls.get_browser()
        ua = random.choice(_USER_AGENTS)
        page = await browser.new_page(user_agent=ua)

        await page.set_extra_http_headers({
            'Accept-Language': 'en-US,en;q=0.9',
        })
        await page.set_viewport_size({"width": 1920, "height": 1080})

        # Remove webdriver property
        await page.add_init_script("""
            Object.defineProperty(navigator, 'webdriver', {
                get: () => undefined
            });
        """)

        return page


class JobScraper:
    """Base scraper class using Playwright with advanced features"""
    
    def __init__(self, source_name: str, base_url: str, job_list_selector: str = None, wait_timeout: int = 30000):
        self.source_name = source_name
        self.base_url = base_url
        self.search_url = base_url
        self.job_list_selector = job_list_selector  # Selector to wait for job list to load
        self.wait_timeout = wait_timeout  # Timeout in milliseconds
        self.max_pages = 3  # Maximum pages to scrape (for pagination)
        self.scroll_delay = 2000  # Delay between scrolls (ms)
        self.max_retries = 2  # Retry page loads on transient failures

    async def scrape(self) -> List[Job]:
        """Scrape jobs from the source. Override in subclasses."""
        raise NotImplementedError

    def parse_job(self, element, job_url: str = None) -> Optional[Job]:
        """Parse a job element. Override in subclasses."""
        raise NotImplementedError
    
    async def handle_infinite_scroll(self, page: Page, max_scrolls: int = 3) -> None:
        """Handle infinite scroll by scrolling down and waiting for new content"""
        try:
            last_height = await page.evaluate("document.body.scrollHeight")
            scrolls = 0

            while scrolls < max_scrolls:
                # Scroll to bottom
                await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                await page.wait_for_timeout(self.scroll_delay)

                # Check if new content loaded
                new_height = await page.evaluate("document.body.scrollHeight")
                if new_height == last_height:
                    break  # No new content

                last_height = new_height
                scrolls += 1
                logger.debug(f"Scrolled {scrolls} times on {self.source_name}")
        except Exception as e:
            logger.warning(f"Error handling infinite scroll on {self.source_name}: {e}")

    async def click_load_more(self, page: Page, button_selector: str = None, max_clicks: int = 5) -> int:
        """Click 'Load More' button if it exists. Returns number of clicks."""
        clicks = 0
        try:
            for _ in range(max_clicks):
                # Try multiple common selectors for "Load More" buttons
                selectors = []
                if button_selector:
                    selectors.append(button_selector)
                selectors.extend([
                    'button:has-text("Load More")',
                    'button:has-text("Show More")',
                    'a:has-text("Load More")',
                    '[class*="load-more"]',
                    '[class*="show-more"]',
                    '[id*="load-more"]',
                    'button[aria-label*="more"]',
                ])

                clicked = False
                for selector in selectors:
                    try:
                        button = await page.query_selector(selector)
                        if button:
                            is_visible = await button.is_visible()
                            if is_visible:
                                await button.click()
                                await page.wait_for_timeout(2000)  # Wait for content to load
                                clicks += 1
                                clicked = True
                                logger.debug(f"Clicked 'Load More' button on {self.source_name} (click {clicks})")
                                break
                    except Exception:
                        continue

                if not clicked:
                    break  # No more buttons to click
        except Exception as e:
            logger.warning(f"Error clicking load more on {self.source_name}: {e}")

        return clicks
    
    async def extract_job_details(self, page: Page, job_url: str) -> Dict[str, str]:
        """Extract detailed information from a job page (salary, location, full description, etc.)"""
        details = {}
        try:
            # Navigate to job page
            await page.goto(job_url, wait_until='domcontentloaded', timeout=15000)
            await page.wait_for_timeout(2000)
            
            content = await page.content()
            soup = BeautifulSoup(content, 'html.parser')
            
            # Try to extract salary
            salary_patterns = [
                r'\$[\d,]+(?:k|K)?(?:\s*-\s*\$?[\d,]+(?:k|K)?)?',
                r'[\d,]+(?:k|K)?\s*USD',
                r'€[\d,]+',
                r'£[\d,]+',
            ]
            text = soup.get_text()
            for pattern in salary_patterns:
                match = re.search(pattern, text)
                if match:
                    details['salary'] = match.group(0)
                    break
            
            # Try to extract location
            location_selectors = [
                '[class*="location"]',
                '[class*="city"]',
                '[data-location]',
                '[itemprop="jobLocation"]',
            ]
            for selector in location_selectors:
                elem = soup.select_one(selector)
                if elem:
                    details['location'] = elem.get_text(strip=True)
                    break
            
            # Extract full description
            desc_selectors = [
                '[class*="description"]',
                '[class*="job-description"]',
                '[id*="description"]',
                'article',
                'main',
            ]
            for selector in desc_selectors:
                elem = soup.select_one(selector)
                if elem:
                    details['full_description'] = elem.get_text(strip=True)[:1000]
                    break
            
        except Exception as e:
            logger.debug(f"Error extracting job details from {job_url}: {e}")
        
        return details
    
    async def get_page_content(self, url: str, take_screenshot: bool = False, screenshot_path: str = None, 
                              handle_scroll: bool = False, handle_load_more: bool = False) -> tuple[Optional[str], Optional[Page]]:
        """Get page content after JavaScript rendering with advanced features

        Args:
            url: URL to load
            take_screenshot: Whether to take a screenshot
            screenshot_path: Path for screenshot
            handle_scroll: Whether to handle infinite scroll
            handle_load_more: Whether to click "Load More" buttons

        Returns:
            tuple: (content, page) - Returns content and page object if take_screenshot is True
        """
        for attempt in range(1, self.max_retries + 1):
            result = await self._load_page(url, take_screenshot, screenshot_path, handle_scroll, handle_load_more)
            content, page = result
            if content is not None:
                return result
            if attempt < self.max_retries:
                wait = attempt * 3
                logger.warning(f"Retry {attempt}/{self.max_retries} for {self.source_name} in {wait}s...")
                await asyncio.sleep(wait)
        return None, None

    async def _load_page(self, url: str, take_screenshot: bool = False, screenshot_path: str = None,
                         handle_scroll: bool = False, handle_load_more: bool = False) -> tuple[Optional[str], Optional[Page]]:
        """Internal: single attempt to load a page and return its content."""
        page = None
        try:
            page = await PlaywrightBrowserManager.create_page()
            logger.info(f"Loading {url}...")
            
            # Use 'domcontentloaded' instead of 'networkidle' to avoid timeouts
            # Add error handling for download triggers (some sites trigger downloads on load)
            try:
                # For Delphi Ventures, use a workaround: fetch with requests first, then set content
                if "delphiventures" in url.lower():
                    try:
                        # Try to fetch the page with requests (doesn't trigger downloads)
                        import requests as req_lib
                        headers = {
                            'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36',
                            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
                            'Accept-Language': 'en-US,en;q=0.9'
                        }
                        response = req_lib.get(url, headers=headers, timeout=15, allow_redirects=True)
                        if response.status_code == 200:
                            # Set the HTML content directly in Playwright
                            await page.set_content(response.text, wait_until='domcontentloaded')
                            await page.wait_for_timeout(3000)
                            logger.info(f"Delphi Ventures loaded via requests workaround")
                        else:
                            raise Exception(f"HTTP {response.status_code}")
                    except Exception as req_error:
                        logger.warning(f"Requests approach failed for Delphi Ventures: {req_error}, trying Playwright...")
                        # Fallback to Playwright with download handling
                        await page.goto(url, wait_until='domcontentloaded', timeout=self.wait_timeout)
                        await page.wait_for_timeout(3000)
                else:
                    await page.goto(url, wait_until='domcontentloaded', timeout=self.wait_timeout)
            except Exception as goto_error:
                error_str = str(goto_error)
                # If it's a download error, try to continue anyway (page might still have loaded)
                if "Download" in error_str or "download" in error_str.lower() or "Download is starting" in error_str:
                    logger.warning(f"Page triggered download for {self.source_name}, checking if content loaded...")
                    # Wait a bit and check if we can get content
                    await page.wait_for_timeout(3000)
                    current_url = page.url
                    if current_url and current_url != "about:blank" and "chrome-error" not in current_url:
                        logger.info(f"Page loaded despite download trigger: {current_url}")
                    else:
                        logger.warning(f"Page did not load properly for {self.source_name}")
                        # For Delphi Ventures, try one more time with request blocking
                        if "delphiventures" in url.lower():
                            try:
                                # Create a new page and try again with download blocking
                                await page.close()
                                page = await PlaywrightBrowserManager.create_page()
                                
                                async def handle_route(route):
                                    request = route.request
                                    if request.resource_type == "other" or "download" in request.url.lower():
                                        await route.abort()
                                    else:
                                        await route.continue_()
                                
                                await page.route("**/*", handle_route)
                                await page.goto(url, wait_until='load', timeout=self.wait_timeout)
                                await page.wait_for_timeout(3000)
                                logger.info(f"Delphi Ventures loaded successfully on retry")
                            except Exception as e:
                                logger.error(f"Delphi Ventures failed on retry: {e}")
                                return None, None
                        else:
                            return None, None
                else:
                    # Re-raise if it's a different error
                    logger.error(f"Error loading page {url}: {goto_error}")
                    raise
            
            # Wait for body to ensure page is ready
            try:
                await page.wait_for_selector('body', timeout=5000)
            except PlaywrightTimeoutError:
                logger.warning(f"Body not found for {self.source_name}, continuing anyway...")
            
            # Wait for job list to load if selector is provided
            if self.job_list_selector:
                try:
                    # Try to wait for job list container
                    await page.wait_for_selector(self.job_list_selector, timeout=10000)
                    logger.info(f"Job list container found for {self.source_name}")
                except PlaywrightTimeoutError:
                    logger.warning(f"Job list selector not found for {self.source_name}, trying generic selectors...")
                    # Fallback: wait for common job-related elements
                    try:
                        await page.wait_for_selector('article, [class*="job"], [class*="listing"]', timeout=5000)
                    except PlaywrightTimeoutError:
                        pass
            
            # Additional wait for content to render
            await page.wait_for_timeout(3000)  # 3 seconds for JS to render
            
            # Handle infinite scroll if requested
            if handle_scroll:
                await self.handle_infinite_scroll(page, max_scrolls=3)
            
            # Handle "Load More" buttons if requested
            if handle_load_more:
                clicks = await self.click_load_more(page, 'button:has-text("Load More")', max_clicks=3)
                if clicks > 0:
                    logger.info(f"Clicked 'Load More' {clicks} time(s) on {self.source_name}")
            
            # Wait a bit more for any lazy-loaded content
            await page.wait_for_timeout(2000)  # 2 seconds
            
            # Take screenshot if requested
            if take_screenshot and screenshot_path:
                try:
                    await page.screenshot(path=screenshot_path, full_page=True)
                    logger.info(f"Debug screenshot saved to {screenshot_path}")
                except Exception as e:
                    logger.warning(f"Failed to take screenshot: {e}")
            
            # Get the rendered HTML
            content = await page.content()
            
            if take_screenshot:
                return content, page  # Return page if screenshot was requested
            else:
                return content, None
            
        except Exception as e:
            logger.error(f"Error loading page {url}: {e}")
            return None, None
        finally:
            if page and not take_screenshot:
                await page.close()


class Web3CareerScraper(JobScraper):
    """Scraper for Web3.career"""
    
    def __init__(self):
        super().__init__(
            "Web3.career",
            "https://web3.career",
            job_list_selector='tbody tr, div.table_row',  # Table rows or table row divs
            wait_timeout=30000
        )
        # Use remote-jobs page (jobs page returns 404)
        self.search_url = "https://web3.career/remote-jobs"

    async def scrape(self) -> List[Job]:
        """Scrape Web3.career with advanced features"""
        jobs = []
        page = None
        try:
            logger.info(f"Scraping {self.source_name}...")
            # Use advanced page loading with scroll support
            content, page = await self.get_page_content(
                self.search_url,
                handle_scroll=True,
                handle_load_more=True
            )
            
            if not content:
                # Try homepage as fallback
                logger.info(f"Trying homepage for {self.source_name}...")
                content, page = await self.get_page_content(
                    self.base_url,
                    handle_scroll=True,
                    handle_load_more=True
                )
            
            if not content:
                return jobs
            
            soup = BeautifulSoup(content, 'html.parser')
            
            # Advanced selector strategy for Web3.career
            job_elements = []
            
            # Strategy 1: Table structure (primary)
            job_elements = soup.select('tbody tr')
            
            # Strategy 2: Div table rows
            if not job_elements:
                job_elements = soup.find_all('div', class_=re.compile(r'table_row|table-row', re.I))
            
            # Strategy 3: Generic row classes
            if not job_elements:
                job_elements = soup.find_all('div', class_=re.compile(r'row', re.I))
            
            # Strategy 4: Table rows without tbody
            if not job_elements:
                job_elements = soup.find_all('tr', class_=re.compile(r'job|listing|row', re.I))
            
            # Strategy 5: Article/div with job classes
            if not job_elements:
                job_elements = soup.find_all(['article', 'div'], class_=re.compile(r'job|listing|card', re.I))
            
            # Strategy 6: Links to job pages
            if not job_elements:
                job_elements = soup.find_all('a', href=re.compile(r'/job/|/jobs/'))
            
            # Strategy 7: Data attributes
            if not job_elements:
                job_elements = soup.find_all(['div', 'li'], attrs={'data-job-id': True}) or \
                              soup.find_all(['div', 'li'], class_=re.compile(r'item|post', re.I))
            
            logger.info(f"Found {len(job_elements)} potential job elements from {self.source_name}")
            
            # Debug: Take screenshot if 0 jobs found
            if len(job_elements) == 0:
                logger.warning("Found 0 jobs. Taking debug screenshot...")
                screenshot_path = "debug_web3_career.png"
                content, page = await self.get_page_content(
                    self.search_url,
                    take_screenshot=True,
                    screenshot_path=screenshot_path,
                    handle_scroll=True
                )
                if not content:
                    content, page = await self.get_page_content(
                        self.base_url,
                        take_screenshot=True,
                        screenshot_path=screenshot_path,
                        handle_scroll=True
                    )
                logger.info(f"Debug screenshot saved to {screenshot_path}")
            
            # Parse all found elements (increased limit)
            for element in job_elements[:50]:  # Increased from 20
                try:
                    job = self.parse_job(element)
                    if job:
                        jobs.append(job)
                except Exception as e:
                    logger.warning(f"Error parsing job element: {e}")
                    continue
            
            # Debug output if elements found but no jobs parsed
            if len(job_elements) > 0 and len(jobs) == 0:
                logger.warning(f"Found {len(job_elements)} elements but parsed 0 jobs. First element HTML:")
                logger.warning(str(job_elements[0])[:1000])
                    
        except Exception as e:
            logger.error(f"Error scraping {self.source_name}: {e}")
        finally:
            if page:
                await page.close()
        
        return jobs

    def parse_job(self, element, job_url: str = None) -> Optional[Job]:
        """Parse a Web3.career job element (tr.table_row structure)"""
        try:
            # Web3.career uses tr.table_row structure
            # Extract title from h2 tag inside the row
            title_elem = element.find('h2')
            if not title_elem:
                return None
            
            title = title_elem.get_text(strip=True)
            if not title or len(title) < 5:  # Skip if title is too short
                return None
            
            # Extract URL from first a tag inside the row
            link = element.find('a', href=True)
            if not link:
                return None
            
            url = link.get('href', '')
            if not url:
                return None
            
            # Handle relative links
            if not url.startswith('http'):
                url = f"{self.base_url}{url}"
            
            # Extract company
            company_elem = element.find(['span', 'div', 'p'], class_=re.compile(r'company', re.I))
            company = company_elem.get_text(strip=True) if company_elem else "Unknown"
            
            # Extract description
            desc_elem = element.find(['p', 'div'], class_=re.compile(r'description|summary', re.I))
            description = desc_elem.get_text(strip=True) if desc_elem else ""
            
            # If description is empty, try to get more text
            if not description:
                description = element.get_text(strip=True)[:500]
            
            # Rank the job
            priority, reason = JobRanker.rank_job(title, description)
            
            if priority == JobPriority.BLACKLISTED:
                return None
            
            return Job(
                title=title,
                company=company,
                url=url,
                description=description[:300],  # Limit description length
                source=self.source_name,
                priority=priority,
                priority_reason=reason
            )
        except Exception as e:
            logger.warning(f"Error parsing job: {e}")
            return None


class CryptoJobsListScraper(JobScraper):
    """Advanced scraper for CryptoJobsList.com with pagination and infinite scroll support"""
    
    def __init__(self):
        super().__init__(
            "CryptoJobsList.com",
            "https://cryptojobslist.com",
            job_list_selector='article, [class*="job"], [class*="listing"], [data-job-id]',
            wait_timeout=30000
        )
        self.search_url = "https://cryptojobslist.com"

    async def scrape(self) -> List[Job]:
        """Scrape CryptoJobsList.com with advanced features"""
        jobs = []
        page = None
        try:
            logger.info(f"Scraping {self.source_name}...")
            
            # Use advanced page loading with scroll and load more support
            content, page = await self.get_page_content(
                self.search_url, 
                take_screenshot=False,
                handle_scroll=True,  # Handle infinite scroll
                handle_load_more=True  # Click "Load More" buttons
            )
            
            if not content:
                return jobs
            
            soup = BeautifulSoup(content, 'html.parser')
            
            # Advanced selector strategy: Try multiple approaches
            job_elements = []
            
            # Strategy 1: Data attributes (most reliable)
            job_elements = soup.find_all(attrs={'data-job-id': True})
            
            # Strategy 2: Semantic HTML
            if not job_elements:
                job_elements = soup.find_all('article')
            
            # Strategy 3: Class-based selectors with multiple patterns
            if not job_elements:
                job_elements = soup.find_all(['div', 'li'], class_=re.compile(r'job|listing|card|item|post', re.I))
            
            # Strategy 4: Links to job pages
            if not job_elements:
                job_elements = soup.find_all('a', href=re.compile(r'/job/|/jobs/|/position/'))
            
            # Strategy 5: Generic selectors
            if not job_elements:
                job_elements = soup.find_all(['div', 'section'], attrs={'data-job': True}) or \
                              soup.find_all(['div', 'li'], class_=re.compile(r'post|entry', re.I))
            
            logger.info(f"Found {len(job_elements)} potential job elements from {self.source_name}")
            
            # Debug: Print first element HTML if elements found
            if len(job_elements) > 0:
                first_element_html = str(job_elements[0])[:500]  # First 500 chars
                logger.debug(f"First element HTML sample: {first_element_html}")
            
            # Parse all found elements (increased limit for better coverage)
            for element in job_elements[:50]:  # Increased from 20 to 50
                try:
                    job = self.parse_job(element)
                    if job:
                        jobs.append(job)
                except Exception as e:
                    logger.warning(f"Error parsing job element: {e}")
                    continue
            
            # Debug output if elements found but no jobs parsed
            if len(job_elements) > 0 and len(jobs) == 0:
                logger.warning(f"Found {len(job_elements)} elements but parsed 0 jobs. First element HTML:")
                logger.warning(str(job_elements[0])[:1000])  # Print first 1000 chars for debugging
                    
        except Exception as e:
            logger.error(f"Error scraping {self.source_name}: {e}")
        finally:
            if page:
                await page.close()
        
        return jobs

    def parse_job(self, element, job_url: str = None) -> Optional[Job]:
        """Parse a CryptoJobsList.com job element with advanced extraction"""
        try:
            # Advanced title extraction: Try multiple strategies
            title = None
            title_elem = None
            
            # Strategy 1: Data attributes
            if not title_elem:
                title_elem = element.find(attrs={'data-job-title': True})
                if title_elem:
                    title = title_elem.get('data-job-title')
            
            # Strategy 2: Heading tags with class patterns
            if not title_elem:
                title_elem = element.find(['h1', 'h2', 'h3', 'h4'], class_=re.compile(r'title|name|job|heading', re.I))
            
            # Strategy 3: Any heading tag
            if not title_elem:
                title_elem = element.find(['h1', 'h2', 'h3', 'h4', 'h5'])
            
            # Strategy 4: Link with job URL pattern
            if not title_elem:
                title_elem = element.find('a', href=re.compile(r'/job/|/jobs/|/position/'))
            
            # Strategy 5: First link in element
            if not title_elem:
                title_elem = element.find('a', href=True)
            
            if title_elem:
                if not title:
                    title = title_elem.get_text(strip=True)
            
            if not title or len(title) < 5:
                return None
            
            # Advanced URL extraction
            url = job_url or self.base_url
            link = element.find('a', href=True)
            if link:
                href = link.get('href', '')
                if href:
                    if not href.startswith('http'):
                        url = f"{self.base_url}{href}" if not href.startswith('/') else f"{self.base_url}{href}"
                    else:
                        url = href
            
            # Advanced company extraction
            company = "Unknown"
            # Strategy 1: Data attributes
            company_elem = element.find(attrs={'data-company': True})
            if company_elem:
                company = company_elem.get('data-company')
            else:
                # Strategy 2: Class-based selectors
                company_elem = element.find(['span', 'div', 'p', 'a'], class_=re.compile(r'company|employer|organization|brand', re.I))
                if company_elem:
                    company = company_elem.get_text(strip=True)
                else:
                    # Strategy 3: Look for company in common patterns
                    text = element.get_text('\n', strip=True)
                    lines = text.split('\n')
                    for line in lines[:5]:  # Check first 5 lines
                        if any(indicator in line.lower() for indicator in ['@', 'at ', 'company:', 'employer:']):
                            # Extract company name
                            parts = re.split(r'[@:]', line, maxsplit=1)
                            if len(parts) > 1:
                                company = parts[-1].strip().split()[0] if parts[-1].strip().split() else company
                                break
            
            # Advanced description extraction
            description = ""
            # Strategy 1: Data attributes
            desc_elem = element.find(attrs={'data-description': True})
            if desc_elem:
                description = desc_elem.get('data-description')
            else:
                # Strategy 2: Class-based selectors
                desc_elem = element.find(['p', 'div', 'span'], class_=re.compile(r'description|summary|excerpt|snippet|text', re.I))
                if desc_elem:
                    description = desc_elem.get_text(strip=True)
            
            # Fallback: Get all text from element
            if not description:
                description = element.get_text(strip=True, separator=' ')[:500]
            
            # Extract posted date if available
            posted_date = None
            date_elem = element.find(['time', 'span', 'div'], class_=re.compile(r'date|posted|time', re.I))
            if date_elem:
                posted_date = date_elem.get_text(strip=True)
                # Try to get datetime attribute
                if date_elem.get('datetime'):
                    posted_date = date_elem.get('datetime')
            
            priority, reason = JobRanker.rank_job(title, description)
            
            if priority == JobPriority.BLACKLISTED:
                return None
            
            return Job(
                title=title,
                company=company,
                url=url,
                description=description[:300],
                source=self.source_name,
                priority=priority,
                priority_reason=reason,
                posted_date=posted_date
            )
        except Exception as e:
            logger.warning(f"Error parsing job: {e}")
            return None


class CryptocurrencyJobsScraper(JobScraper):
    """Scraper for CryptocurrencyJobs.co"""
    
    def __init__(self):
        super().__init__(
            "CryptocurrencyJobs.co",
            "https://cryptocurrencyjobs.co",
            job_list_selector='article, [class*="job"], [class*="listing"]',
            wait_timeout=30000
        )
        self.search_url = "https://cryptocurrencyjobs.co"

    async def scrape(self) -> List[Job]:
        """Scrape CryptocurrencyJobs.co with advanced features"""
        jobs = []
        page = None
        try:
            logger.info(f"Scraping {self.source_name}...")
            # Use advanced page loading
            content, page = await self.get_page_content(
                self.search_url,
                handle_scroll=True,
                handle_load_more=True
            )
            
            if not content:
                return jobs
            
            soup = BeautifulSoup(content, 'html.parser')
            
            # Advanced selector strategy
            job_elements = []
            
            # Strategy 1: H2 headings in main (original approach)
            main_elem = soup.find('main')
            if main_elem:
                h2_elements = main_elem.find_all('h2')
                # Convert H2 elements to job elements for consistent parsing
                for h2 in h2_elements:
                    title_text = h2.get_text(strip=True)
                    if title_text and len(title_text) >= 5:
                        if 'talent collective' not in title_text.lower() and 'subscribe' not in title_text.lower():
                            job_elements.append(h2)
            
            # Strategy 2: Article elements
            if not job_elements:
                job_elements = soup.find_all('article')
            
            # Strategy 3: Links to job pages
            if not job_elements:
                job_elements = soup.find_all('a', href=re.compile(r'/job/|/jobs/'))
            
            # Strategy 4: Data attributes
            if not job_elements:
                job_elements = soup.find_all(['div', 'li'], attrs={'data-job-id': True})
            
            logger.info(f"Found {len(job_elements)} potential job elements from {self.source_name}")
            
            for element in job_elements[:50]:  # Increased limit
                try:
                    # Handle H2 elements specially
                    if element.name == 'h2':
                        title_text = element.get_text(strip=True)
                        if not title_text or len(title_text) < 5:
                            continue
                        if 'talent collective' in title_text.lower() or 'subscribe' in title_text.lower():
                            continue
                        
                        # Find parent <a> tag or closest ancestor
                        link = element.find_parent('a')
                        if not link:
                            parent = element.parent
                            if parent:
                                link = parent.find('a')
                        
                        url = self.base_url
                        if link and link.get('href'):
                            href = link['href']
                            if not href.startswith('http'):
                                url = f"{self.base_url}{href}"
                            else:
                                url = href
                        
                        job = self.parse_job_from_h2(title_text, url)
                    else:
                        job = self.parse_job(element)
                    
                    if job:
                        jobs.append(job)
                except Exception as e:
                    logger.warning(f"Error parsing job element: {e}")
                    continue
                    
        except Exception as e:
            logger.error(f"Error scraping {self.source_name}: {e}")
        finally:
            if page:
                await page.close()
        
        return jobs
    
    def parse_job_from_h2(self, title: str, url: str) -> Optional[Job]:
        """Parse job from H2 title and URL"""
        try:
            if not title or len(title) < 5:
                return None
            
            # Extract company (try to parse from title or use default)
            company = "Unknown"
            # Look for common patterns like "Title @ Company" or "Company - Title"
            if '@' in title:
                parts = title.split('@')
                if len(parts) > 1:
                    company = parts[-1].strip()
                    title = parts[0].strip()
            elif ' - ' in title:
                parts = title.split(' - ', 1)
                if len(parts) > 1:
                    title = parts[0].strip()
                    company = parts[1].strip()
            
            # Use title as description (limited)
            description = title[:300]
            
            # Rank the job
            priority, reason = JobRanker.rank_job(title, description)
            
            if priority == JobPriority.BLACKLISTED:
                return None
            
            return Job(
                title=title,
                company=company,
                url=url,
                description=description[:300],
                source=self.source_name,
                priority=priority,
                priority_reason=reason
            )
        except Exception as e:
            logger.warning(f"Error parsing job from H2: {e}")
            return None

    def parse_job(self, element, job_url: str = None) -> Optional[Job]:
        """Parse a CryptocurrencyJobs.co job element"""
        try:
            title_elem = element.find(['h2', 'h3', 'h4', 'a'], class_=re.compile(r'title|name|job', re.I))
            if not title_elem:
                title_elem = element.find('a', href=re.compile(r'/job/'))
            
            if not title_elem:
                return None
            
            title = title_elem.get_text(strip=True)
            if not title or len(title) < 5:
                return None
            
            link = element.find('a', href=True)
            if link:
                url = link['href']
                if not url.startswith('http'):
                    url = f"{self.base_url}{url}"
            else:
                url = job_url or self.base_url
            
            company_elem = element.find(['span', 'div', 'p'], class_=re.compile(r'company|employer', re.I))
            company = company_elem.get_text(strip=True) if company_elem else "Unknown"
            
            desc_elem = element.find(['p', 'div'], class_=re.compile(r'description|summary|excerpt', re.I))
            description = desc_elem.get_text(strip=True) if desc_elem else ""
            
            if not description:
                description = element.get_text(strip=True)[:500]
            
            priority, reason = JobRanker.rank_job(title, description)
            
            if priority == JobPriority.BLACKLISTED:
                return None
            
            return Job(
                title=title,
                company=company,
                url=url,
                description=description[:300],
                source=self.source_name,
                priority=priority,
                priority_reason=reason
            )
        except Exception as e:
            logger.warning(f"Error parsing job: {e}")
            return None


class SolanaJobsScraper(JobScraper):
    """Scraper for Solana jobs board (Getro / Next.js).

    The site embeds all job data as JSON inside a __NEXT_DATA__ script tag,
    so we fetch the HTML with a simple HTTP request (no Playwright needed)
    and parse the embedded JSON directly.  This is faster and avoids the
    403 / download-trigger issues that blocked the old Playwright approach.
    """

    def __init__(self):
        super().__init__(
            "Solana Jobs",
            "https://jobs.solana.com",
        )
        self.search_url = "https://jobs.solana.com/jobs"

    async def scrape(self) -> List[Job]:
        jobs: List[Job] = []
        try:
            logger.info(f"Scraping {self.source_name} (JSON mode)...")
            headers = {
                "User-Agent": random.choice(_USER_AGENTS),
                "Accept": "text/html,application/xhtml+xml",
                "Accept-Language": "en-US,en;q=0.9",
            }
            resp = requests.get(self.search_url, headers=headers, timeout=20)
            resp.raise_for_status()

            # Extract __NEXT_DATA__ JSON blob
            soup = BeautifulSoup(resp.text, "html.parser")
            script_tag = soup.find("script", id="__NEXT_DATA__")
            if not script_tag or not script_tag.string:
                logger.warning(f"__NEXT_DATA__ not found on {self.source_name}")
                return jobs

            next_data = json.loads(script_tag.string)
            job_list = (
                next_data.get("props", {})
                .get("pageProps", {})
                .get("initialState", {})
                .get("jobs", {})
                .get("found", [])
            )
            logger.info(f"Found {len(job_list)} jobs in Solana __NEXT_DATA__")

            for entry in job_list:
                try:
                    job = self._parse_entry(entry)
                    if job:
                        jobs.append(job)
                except Exception as e:
                    logger.warning(f"Error parsing Solana job entry: {e}")

            logger.info(f"Successfully scraped {len(jobs)} jobs from {self.source_name}")
        except Exception as e:
            logger.error(f"Error scraping {self.source_name}: {e}")
        return jobs

    def _parse_entry(self, entry: dict) -> Optional[Job]:
        """Parse a single job dict from the Getro JSON payload."""
        title = entry.get("title", "").strip()
        if not title or len(title) < 5:
            return None

        # Build URL — prefer the external application link, fall back to board link
        url = entry.get("url", "")
        if not url:
            slug = entry.get("slug", "")
            url = f"{self.base_url}/jobs/{slug}" if slug else self.base_url

        # Company
        org = entry.get("organization", {})
        company = org.get("name", "Unknown")

        # Location + work mode
        locations = entry.get("locations", [])
        work_mode = entry.get("workMode", "")
        location_str = ", ".join(locations[:3]) if locations else "Remote"
        if work_mode:
            location_str += f" ({work_mode.replace('_', ' ')})"

        # Seniority (may be null)
        seniority = entry.get("seniority") or ""

        # Skills as description stand-in
        skills = entry.get("skills", [])
        desc_parts = []
        if seniority:
            desc_parts.append(f"Seniority: {seniority}")
        if location_str:
            desc_parts.append(f"Location: {location_str}")
        if skills:
            desc_parts.append(f"Skills: {', '.join(skills[:10])}")
        description = " | ".join(desc_parts) if desc_parts else title

        priority, reason = JobRanker.rank_job(title, description)
        if priority == JobPriority.BLACKLISTED:
            return None

        return Job(
            title=title,
            company=company,
            url=url,
            description=description[:300],
            source=self.source_name,
            priority=priority,
            priority_reason=reason,
        )


class DelphiVenturesScraper(JobScraper):
    """Scraper for Delphi Ventures job board (Getro-powered)"""
    
    def __init__(self):
        super().__init__(
            "Delphi Ventures",
            "https://jobs.delphiventures.io",
            job_list_selector='[class*="job"], [class*="listing"], article',
            wait_timeout=30000
        )
        # Use filtered URL for IT roles only
        self.search_url = "https://jobs.delphiventures.io/jobs?filter=eyJqb2JfZnVuY3Rpb25zIjpbIklUIl19"

    async def scrape(self) -> List[Job]:
        """Scrape Delphi Ventures job board with advanced features"""
        jobs = []
        page = None
        try:
            logger.info(f"Scraping {self.source_name}...")
            # Use advanced page loading with longer wait for Getro boards
            content, page = await self.get_page_content(
                self.search_url,
                handle_scroll=True,
                handle_load_more=True
            )
            
            if not content:
                logger.warning(f"No content retrieved from {self.source_name}")
                return jobs
            
            soup = BeautifulSoup(content, 'html.parser')
            
            # Getro boards use specific structure - try multiple strategies
            job_elements = []
            
            # Strategy 1: Look for Getro-specific job cards (usually in a list)
            # Getro often uses <li> elements with job data
            job_elements = soup.find_all('li', class_=re.compile(r'job|listing|card|item|result', re.I))
            
            # Strategy 2: Article elements (Getro standard)
            if not job_elements:
                job_elements = soup.find_all('article')
            
            # Strategy 3: Divs with Getro-specific classes
            if not job_elements:
                # Getro often uses divs with data attributes or specific classes
                job_elements = soup.find_all('div', class_=re.compile(r'job|listing|card|item|result|position', re.I))
            
            # Strategy 4: Look for job links in structured format
            if not job_elements:
                # Getro job links usually have href containing /jobs/ or job ID
                job_links = soup.find_all('a', href=re.compile(r'/jobs/|/job/|/positions/'))
                if job_links:
                    # Get parent containers of job links
                    for link in job_links:
                        parent = link.find_parent(['li', 'div', 'article'])
                        if parent and parent not in job_elements:
                            job_elements.append(parent)
            
            # Strategy 5: Data attributes (Getro sometimes uses these)
            if not job_elements:
                job_elements = soup.find_all(['div', 'li'], attrs={'data-job-id': True})
                if not job_elements:
                    job_elements = soup.find_all(['div', 'li'], attrs={'data-position-id': True})
            
            logger.info(f"Found {len(job_elements)} potential job elements from {self.source_name}")
            
            # Debug: Log first element structure if found
            if len(job_elements) > 0:
                first_elem = str(job_elements[0])[:300]
                logger.debug(f"First job element sample: {first_elem}")
            else:
                # Take screenshot for debugging
                logger.warning(f"No job elements found. Page might not have loaded correctly.")
                if page:
                    try:
                        await page.screenshot(path="debug_delphi_ventures.png", full_page=True)
                        logger.info("Debug screenshot saved to debug_delphi_ventures.png")
                    except Exception:
                        pass
            
            for element in job_elements[:50]:  # Increased limit
                try:
                    job = self.parse_job(element)
                    if job:
                        jobs.append(job)
                        logger.debug(f"Parsed Delphi job: {job.title} @ {job.company}")
                except Exception as e:
                    logger.warning(f"Error parsing job element: {e}")
                    continue
                    
        except Exception as e:
            logger.error(f"Error scraping {self.source_name}: {e}", exc_info=True)
        finally:
            if page:
                await page.close()
        
        logger.info(f"Successfully scraped {len(jobs)} jobs from {self.source_name}")
        return jobs

    def parse_job(self, element, job_url: str = None) -> Optional[Job]:
        """Parse a Delphi Ventures job element with improved extraction"""
        try:
            # Getro structure: Look for job title in multiple places
            title = None
            title_elem = None
            
            # Strategy 1: Look for h2-h5 headings (most common)
            title_elem = element.find(['h2', 'h3', 'h4', 'h5', 'h6'])
            
            # Strategy 2: Look for title in link text
            if not title_elem:
                title_elem = element.find('a', href=re.compile(r'/jobs/|/job/'))
            
            # Strategy 3: Look for any link with meaningful text
            if not title_elem:
                links = element.find_all('a', href=True)
                for link in links:
                    text = link.get_text(strip=True)
                    if text and len(text) > 5 and len(text) < 200:
                        # Skip navigation links
                        if not any(skip in text.lower() for skip in ['subscribe', 'read more', 'apply', 'view all']):
                            title_elem = link
                            break
            
            if not title_elem:
                return None
            
            title = title_elem.get_text(strip=True)
            if not title or len(title) < 5:
                return None
            
            # Skip if it's clearly not a job
            if any(skip in title.lower() for skip in ['subscribe', 'get in touch', 'privacy', 'cookie', 'read more about']):
                return None
            
            # Extract URL - prioritize job detail links
            url = job_url or self.base_url
            link = element.find('a', href=re.compile(r'/jobs/|/job/'))
            if link and link.get('href'):
                href = link['href']
                if not href.startswith('http'):
                    url = f"{self.base_url}{href}" if not href.startswith('/') else f"{self.base_url}{href}"
                else:
                    url = href
            else:
                # Fallback: find any link
                link = element.find('a', href=True)
                if link and link.get('href'):
                    href = link['href']
                    if not href.startswith('http'):
                        url = f"{self.base_url}{href}" if not href.startswith('/') else f"{self.base_url}{href}"
                    else:
                        url = href
            
            # Extract company name - improved strategy
            company = "Unknown"
            
            # Strategy 1: Look for company logo or brand element
            company_elem = element.find(['img'], alt=True)
            if company_elem and company_elem.get('alt'):
                alt_text = company_elem.get('alt', '')
                if alt_text and len(alt_text) < 50:
                    company = alt_text.strip()
            
            # Strategy 2: Look for company in structured elements
            if company == "Unknown":
                company_elem = element.find(['span', 'div', 'p', 'h3', 'h4'], class_=re.compile(r'company|employer|organization|brand|logo', re.I))
                if company_elem:
                    company = company_elem.get_text(strip=True)
            
            # Strategy 3: Look for company name in text structure (Getro often has "CompanyName\nJob Title")
            if company == "Unknown":
                # Get all text and split by newlines
                full_text = element.get_text('\n', strip=True)
                lines = [line.strip() for line in full_text.split('\n') if line.strip()]
                
                # Company is usually before the title in the structure
                title_idx = -1
                for i, line in enumerate(lines):
                    if title.lower() in line.lower() or line.lower() in title.lower():
                        title_idx = i
                        break
                
                # Company is likely the line before title, or first line
                if title_idx > 0:
                    company = lines[title_idx - 1]
                elif len(lines) > 0:
                    # First line might be company
                    first_line = lines[0]
                    if first_line != title and len(first_line) < 50:
                        company = first_line
            
            # Strategy 4: Extract from URL if it contains company name
            if company == "Unknown" and '/jobs/' in url:
                # Sometimes URL is like /jobs/company-name/job-title
                url_parts = url.split('/jobs/')
                if len(url_parts) > 1:
                    path_parts = url_parts[1].split('/')
                    if len(path_parts) > 0:
                        potential_company = path_parts[0].replace('-', ' ').title()
                        # Filter out IDs and invalid company names
                        if len(potential_company) < 50 and not potential_company.isdigit() and '#' not in potential_company:
                            company = potential_company
            
            # Clean up company name - remove IDs and invalid patterns
            if company and company != "Unknown":
                # Remove patterns like "63861016 Technical Support Engineer#Content"
                if '#' in company:
                    company = company.split('#')[0].strip()
                # Remove leading numbers
                company = re.sub(r'^\d+\s+', '', company)
                # If company looks like an ID or invalid, set to Unknown
                if company.isdigit() or len(company) < 2 or company.lower() == 'unknown':
                    company = "Unknown"
            
            # Extract description - get more context
            description = ""
            desc_elem = element.find(['p', 'div'], class_=re.compile(r'description|summary|excerpt|text', re.I))
            if desc_elem:
                description = desc_elem.get_text(strip=True)
            
            # Fallback: get all text but exclude title and company
            if not description:
                all_text = element.get_text(strip=True, separator=' ')
                # Remove title and company from description
                description = all_text.replace(title, '').replace(company, '').strip()[:500]
            
            # If still no description, use title
            if not description:
                description = title
            
            # Rank the job
            priority, reason = JobRanker.rank_job(title, description)
            
            if priority == JobPriority.BLACKLISTED:
                return None
            
            return Job(
                title=title,
                company=company,
                url=url,
                description=description[:300],
                source=self.source_name,
                priority=priority,
                priority_reason=reason
            )
        except Exception as e:
            logger.warning(f"Error parsing job: {e}")
            return None


class CryptoJobsScraper(JobScraper):
    """Scraper for CryptoJobs.com"""
    
    def __init__(self):
        super().__init__(
            "CryptoJobs.com",
            "https://www.cryptojobs.com",
            job_list_selector='div[class*="job"], article, li[class*="job"]',
            wait_timeout=30000
        )
        self.search_url = "https://www.cryptojobs.com/jobs"

    async def scrape(self) -> List[Job]:
        """Scrape CryptoJobs.com job listings"""
        jobs = []
        page = None
        try:
            logger.info(f"Scraping {self.source_name}...")
            
            page = await PlaywrightBrowserManager.create_page()
            await page.goto(self.search_url, wait_until='domcontentloaded', timeout=30000)
            
            # Wait for job listings to load
            try:
                await page.wait_for_selector('article, div[class*="job"], li[class*="job"]', timeout=10000)
            except PlaywrightTimeoutError:
                logger.warning(f"Timeout waiting for job listings on {self.source_name}")
            
            await page.wait_for_timeout(2000)
            
            # Handle scroll to load more jobs
            await self.handle_infinite_scroll(page)
            
            content = await page.content()
            
            if not content:
                logger.warning(f"No content retrieved from {self.source_name}")
                return jobs
            
            soup = BeautifulSoup(content, 'html.parser')
            
            # Try multiple selectors for job listings
            job_elements = []
            
            # Strategy 1: Articles (common structure)
            job_elements = soup.find_all('article')
            
            # Strategy 2: Divs with job-related classes
            if not job_elements:
                job_elements = soup.find_all('div', class_=re.compile(r'job|card|listing|position', re.I))
            
            # Strategy 3: List items
            if not job_elements:
                job_elements = soup.find_all('li', class_=re.compile(r'job|card|listing', re.I))
            
            # Strategy 4: Links to job pages
            if not job_elements:
                job_elements = soup.find_all('a', href=re.compile(r'/job/|/jobs/'))
            
            logger.info(f"Found {len(job_elements)} potential job elements from {self.source_name}")
            
            if not job_elements:
                logger.warning(f"No job elements found on {self.source_name}")
                screenshot_path = f"debug_CryptoJobs.png"
                await page.screenshot(path=screenshot_path, full_page=True)
                logger.info(f"Debug screenshot saved to {screenshot_path}")
            
            parsed_count = 0
            for element in job_elements[:100]:
                try:
                    job = self.parse_job(element)
                    if job:
                        jobs.append(job)
                        parsed_count += 1
                except Exception as e:
                    logger.warning(f"Error parsing job element: {e}")
                    continue
            logger.info(f"Successfully scraped {parsed_count} jobs from {self.source_name}")
                    
        except Exception as e:
            logger.error(f"Error scraping {self.source_name}: {e}")
        finally:
            if page:
                await page.close()
        
        return jobs

    def parse_job(self, element, job_url: str = None) -> Optional[Job]:
        """Parse a CryptoJobs.com job element"""
        try:
            # Extract URL
            url = job_url
            link_elem = element.find('a', href=True)
            if link_elem and link_elem.get('href'):
                href = link_elem['href']
                if not href.startswith('http'):
                    url = f"{self.base_url}{href}"
                else:
                    url = href
            
            if not url:
                return None
            
            # Extract title
            title = "Unknown Title"
            title_elem = element.find(['h2', 'h3', 'h4'])
            if title_elem:
                title = title_elem.get_text(strip=True)
            elif link_elem:
                title = link_elem.get_text(strip=True)
            
            if not title or len(title) < 5:
                return None
            
            # Skip if it's clearly not a job
            if any(skip in title.lower() for skip in ['subscribe', 'sign up', 'get started', 'premium']):
                return None
            
            # Extract company
            company = "Unknown"
            company_elem = element.find(['span', 'div', 'p'], class_=re.compile(r'company|employer', re.I))
            if company_elem:
                company = company_elem.get_text(strip=True)
            
            # Extract description
            description = ""
            desc_elem = element.find(['p', 'div'], class_=re.compile(r'description|summary|excerpt', re.I))
            if desc_elem:
                description = desc_elem.get_text(strip=True)
            else:
                description = element.get_text(strip=True, separator=' ')[:500]
            
            priority, reason = JobRanker.rank_job(title, description)
            
            if priority == JobPriority.BLACKLISTED:
                return None
            
            return Job(
                title=title,
                company=company,
                url=url,
                description=description[:300],
                source=self.source_name,
                priority=priority,
                priority_reason=reason
            )
        except Exception as e:
            logger.warning(f"Error parsing job: {e}")
            return None


class FindCryptoJobsScraper(JobScraper):
    """Scraper for FindCryptoJobs.xyz - aggregator site"""
    
    def __init__(self):
        super().__init__(
            "FindCryptoJobs.xyz",
            "https://www.findcryptojobs.xyz",
            job_list_selector='div[class*="job"], article, li[class*="job"]',
            wait_timeout=30000
        )
        self.search_url = "https://www.findcryptojobs.xyz"

    async def scrape(self) -> List[Job]:
        """Scrape FindCryptoJobs.xyz job listings"""
        jobs = []
        page = None
        try:
            logger.info(f"Scraping {self.source_name}...")
            
            page = await PlaywrightBrowserManager.create_page()
            await page.goto(self.search_url, wait_until='domcontentloaded', timeout=30000)
            
            # Wait for job listings to load
            try:
                await page.wait_for_selector('article, div[class*="job"], table', timeout=10000)
            except PlaywrightTimeoutError:
                logger.warning(f"Timeout waiting for job listings on {self.source_name}")
            
            await page.wait_for_timeout(2000)
            
            # Handle scroll to load more jobs
            await self.handle_infinite_scroll(page)
            
            content = await page.content()
            
            if not content:
                logger.warning(f"No content retrieved from {self.source_name}")
                return jobs
            
            soup = BeautifulSoup(content, 'html.parser')
            
            # Try multiple selectors for job listings
            job_elements = []
            
            # Strategy 1: Table rows (common for aggregators)
            job_elements = soup.find_all('tr', class_=re.compile(r'job|row', re.I))
            if not job_elements:
                job_elements = soup.find_all('tr')[1:]  # Skip header row
            
            # Strategy 2: Articles
            if not job_elements:
                job_elements = soup.find_all('article')
            
            # Strategy 3: Divs with job-related classes
            if not job_elements:
                job_elements = soup.find_all('div', class_=re.compile(r'job|card|listing', re.I))
            
            # Strategy 4: List items
            if not job_elements:
                job_elements = soup.find_all('li', class_=re.compile(r'job|card|listing', re.I))
            
            logger.info(f"Found {len(job_elements)} potential job elements from {self.source_name}")
            
            if not job_elements:
                logger.warning(f"No job elements found on {self.source_name}")
                screenshot_path = f"debug_FindCryptoJobs.png"
                await page.screenshot(path=screenshot_path, full_page=True)
                logger.info(f"Debug screenshot saved to {screenshot_path}")
            
            parsed_count = 0
            for element in job_elements[:100]:
                try:
                    job = self.parse_job(element)
                    if job:
                        jobs.append(job)
                        parsed_count += 1
                except Exception as e:
                    logger.warning(f"Error parsing job element: {e}")
                    continue
            logger.info(f"Successfully scraped {parsed_count} jobs from {self.source_name}")
                    
        except Exception as e:
            logger.error(f"Error scraping {self.source_name}: {e}")
        finally:
            if page:
                await page.close()
        
        return jobs

    def parse_job(self, element, job_url: str = None) -> Optional[Job]:
        """Parse a FindCryptoJobs.xyz job element"""
        try:
            # Extract URL
            url = job_url
            link_elem = element.find('a', href=True)
            if link_elem and link_elem.get('href'):
                href = link_elem['href']
                if not href.startswith('http'):
                    url = f"{self.base_url}{href}"
                else:
                    url = href
            
            if not url:
                return None
            
            # Extract title
            title = "Unknown Title"
            # For table rows, title is usually in first cell
            title_elem = element.find(['h2', 'h3', 'h4', 'td', 'a'])
            if title_elem:
                title = title_elem.get_text(strip=True)
            elif link_elem:
                title = link_elem.get_text(strip=True)
            
            if not title or len(title) < 5:
                return None
            
            # Skip if it's clearly not a job
            if any(skip in title.lower() for skip in ['subscribe', 'sign up', 'advertisement']):
                return None
            
            # Extract company
            company = "Unknown"
            # For table rows, company is usually in second cell
            cells = element.find_all('td')
            if len(cells) >= 2:
                company = cells[1].get_text(strip=True)
            else:
                company_elem = element.find(['span', 'div', 'p'], class_=re.compile(r'company|employer', re.I))
                if company_elem:
                    company = company_elem.get_text(strip=True)
            
            # Extract description
            description = ""
            desc_elem = element.find(['p', 'div'], class_=re.compile(r'description|summary', re.I))
            if desc_elem:
                description = desc_elem.get_text(strip=True)
            else:
                description = element.get_text(strip=True, separator=' ')[:500]
            
            priority, reason = JobRanker.rank_job(title, description)
            
            if priority == JobPriority.BLACKLISTED:
                return None
            
            return Job(
                title=title,
                company=company,
                url=url,
                description=description[:300],
                source=self.source_name,
                priority=priority,
                priority_reason=reason
            )
        except Exception as e:
            logger.warning(f"Error parsing job: {e}")
            return None


class BondexScraper(JobScraper):
    """Scraper for Bondex Network jobs (Web3/crypto-focused board)"""

    def __init__(self):
        super().__init__(
            "Bondex Network",
            "https://network.bondex.app",
            job_list_selector='[data-testid*="job"], a[href*="/jobs/"]',
            wait_timeout=30000
        )
        # Use jobs page with empty search/location to list all jobs
        self.search_url = "https://network.bondex.app/jobs?search=&location="

    async def scrape(self) -> List[Job]:
        """Scrape Bondex Network job listings"""
        jobs: List[Job] = []
        page: Optional[Page] = None
        try:
            logger.info(f"Scraping {self.source_name}...")

            page = await PlaywrightBrowserManager.create_page()
            await page.goto(self.search_url, wait_until='domcontentloaded', timeout=self.wait_timeout)

            # Wait for job listings to load
            try:
                await page.wait_for_selector(
                    '[data-testid*="job"], a[href*="/jobs/"], [class*="job-card"], [class*="jobCard"]',
                    timeout=15000
                )
            except Exception:
                logger.warning(f"Timeout waiting for job listings on {self.source_name}")

            # Give some extra time for dynamic content
            await page.wait_for_timeout(2000)

            # Handle scrolling and potential "Load more" patterns
            await self.handle_infinite_scroll(page)
            await self.click_load_more(page)

            content = await page.content()
            if not content:
                logger.warning(f"No content retrieved from {self.source_name}")
                return jobs

            soup = BeautifulSoup(content, 'html.parser')

            job_elements = []

            # Strategy 1: Explicit job test IDs/cards
            job_elements = soup.select('[data-testid*="job"], [class*="job-card"], [class*="JobCard"]')

            # Strategy 2: Links to individual job pages
            if not job_elements:
                job_elements = soup.find_all(
                    'a',
                    href=re.compile(r'/jobs/[a-zA-Z0-9\-]+')
                )

            # Strategy 3: Generic cards that link to /jobs/
            if not job_elements:
                job_elements = [
                    card for card in soup.find_all(['div', 'article', 'li'], class_=re.compile(r'card|job', re.I))
                    if card.find('a', href=re.compile(r'/jobs/'))
                ]

            logger.info(f"Found {len(job_elements)} potential job elements from {self.source_name}")

            parsed_count = 0
            for element in job_elements[:100]:
                try:
                    job = self.parse_job(element)
                    if job:
                        jobs.append(job)
                        parsed_count += 1
                except Exception as e:
                    logger.warning(f"Error parsing job element on {self.source_name}: {e}")
                    continue

            logger.info(f"Successfully scraped {parsed_count} jobs from {self.source_name}")

        except Exception as e:
            logger.error(f"Error scraping {self.source_name}: {e}")
        finally:
            if page:
                await page.close()

        return jobs

    def parse_job(self, element, job_url: str = None) -> Optional[Job]:
        """Parse a Bondex Network job element"""
        try:
            # Determine the anchor element representing the job
            link_elem = None
            if element.name == 'a' and element.get('href'):
                link_elem = element
            else:
                link_elem = element.find('a', href=True)

            if not link_elem or not link_elem.get('href'):
                return None

            href = link_elem['href']
            # Skip navigation or non-job links
            if 'search=' in href or 'location=' in href:
                return None

            if not href.startswith('http'):
                url = f"{self.base_url}{href}"
            else:
                url = href

            # Walk up a few levels to find the full job card container
            container = element
            for _ in range(3):
                if container and container.parent:
                    container = container.parent
                else:
                    break

            if not container:
                container = element

            # Extract title
            title = ""

            title_elem = container.find(['h2', 'h3', 'h4'])
            if not title_elem and link_elem:
                title_elem = link_elem.find(['h2', 'h3', 'h4'])

            if not title_elem and link_elem:
                # Sometimes the text on the link itself is the title
                title = link_elem.get_text(strip=True)
            elif title_elem:
                title = title_elem.get_text(strip=True)

            if not title:
                # Fallback: use a trimmed chunk of container text
                title = container.get_text(strip=True)[:120]

            if not title or len(title) < 5:
                return None

            # Skip obvious non-job links
            lower_title = title.lower()
            if any(skip in lower_title for skip in ['sign in', 'sign up', 'login', 'download app', 'get the app']):
                return None

            # Extract company
            company = "Unknown"
            company_elem = container.find(
                ['span', 'div', 'p'],
                class_=re.compile(r'company|employer|org|organization', re.I)
            )

            if not company_elem:
                # Heuristic: second span/div inside the card often holds the company name
                spans = container.find_all(['span', 'div', 'p'], recursive=True)
                if len(spans) >= 2:
                    company = spans[1].get_text(strip=True)
            else:
                company = company_elem.get_text(strip=True)

            # Extract description (short summary from the card)
            description = ""
            desc_elem = container.find(
                ['p', 'div'],
                class_=re.compile(r'description|summary|details|body', re.I)
            )
            if desc_elem:
                description = desc_elem.get_text(strip=True)
            else:
                # Fallback: longer snippet from container text
                description = container.get_text(strip=True, separator=' ')[:500]

            # Rank job using existing crypto JobRanker
            priority, reason = JobRanker.rank_job(title, description)
            if priority == JobPriority.BLACKLISTED:
                return None

            return Job(
                title=title,
                company=company,
                url=url,
                description=description[:300],
                source=self.source_name,
                priority=priority,
                priority_reason=reason
            )
        except Exception as e:
            logger.warning(f"Error parsing job on {self.source_name}: {e}")
            return None


class RemoteOKScraper(JobScraper):
    """Scraper for RemoteOK - crypto/remote jobs"""
    
    def __init__(self):
        super().__init__(
            "RemoteOK",
            "https://remoteok.com",
            job_list_selector='tr.job',
            wait_timeout=30000
        )
        # Use crypto tag for filtered results
        self.search_url = "https://remoteok.com/remote-crypto-jobs"

    async def scrape(self) -> List[Job]:
        """Scrape RemoteOK crypto jobs"""
        jobs = []
        page = None
        try:
            logger.info(f"Scraping {self.source_name}...")
            
            page = await PlaywrightBrowserManager.create_page()
            await page.goto(self.search_url, wait_until='domcontentloaded', timeout=30000)
            
            # Wait for job table to load
            try:
                await page.wait_for_selector('tr.job, table.jobs', timeout=10000)
            except PlaywrightTimeoutError:
                logger.warning(f"Timeout waiting for job listings on {self.source_name}")
            
            await page.wait_for_timeout(2000)
            
            # Handle scroll
            await self.handle_infinite_scroll(page)
            
            content = await page.content()
            
            if not content:
                logger.warning(f"No content retrieved from {self.source_name}")
                return jobs
            
            soup = BeautifulSoup(content, 'html.parser')
            
            # RemoteOK uses table rows for jobs
            job_elements = soup.find_all('tr', class_='job')
            
            # Fallback: all tr elements in the jobs table
            if not job_elements:
                jobs_table = soup.find('table', class_='jobs')
                if jobs_table:
                    job_elements = jobs_table.find_all('tr')[1:]  # Skip header
            
            logger.info(f"Found {len(job_elements)} potential job elements from {self.source_name}")
            
            if not job_elements:
                logger.warning(f"No job elements found on {self.source_name}")
                screenshot_path = f"debug_RemoteOK.png"
                await page.screenshot(path=screenshot_path, full_page=True)
                logger.info(f"Debug screenshot saved to {screenshot_path}")
            
            parsed_count = 0
            for element in job_elements[:100]:
                try:
                    job = self.parse_job(element)
                    if job:
                        jobs.append(job)
                        parsed_count += 1
                except Exception as e:
                    logger.warning(f"Error parsing job element: {e}")
                    continue
            logger.info(f"Successfully scraped {parsed_count} jobs from {self.source_name}")
                    
        except Exception as e:
            logger.error(f"Error scraping {self.source_name}: {e}")
        finally:
            if page:
                await page.close()
        
        return jobs

    def parse_job(self, element, job_url: str = None) -> Optional[Job]:
        """Parse a RemoteOK job element"""
        try:
            # Skip header rows and ads
            if element.get('class') and any(cls in ['header', 'ad', 'separator'] for cls in element.get('class', [])):
                return None
            
            # Extract URL
            url = job_url
            link_elem = element.find('a', href=True, itemprop='url')
            if not link_elem:
                link_elem = element.find('a', href=True)
            
            if link_elem and link_elem.get('href'):
                href = link_elem['href']
                if not href.startswith('http'):
                    url = f"{self.base_url}{href}"
                else:
                    url = href
            
            if not url:
                return None
            
            # Extract title
            title = "Unknown Title"
            title_elem = element.find('h2', itemprop='title')
            if not title_elem:
                title_elem = element.find(['h2', 'h3', 'td'], class_=re.compile(r'title|position', re.I))
            if title_elem:
                title = title_elem.get_text(strip=True)
            
            if not title or len(title) < 5:
                return None
            
            # Extract company
            company = "Unknown"
            company_elem = element.find('h3', itemprop='name')
            if not company_elem:
                company_elem = element.find(['h3', 'span', 'td'], class_=re.compile(r'company|employer', re.I))
            if company_elem:
                company = company_elem.get_text(strip=True)
            
            # Extract tags/description
            description = ""
            tags_elem = element.find_all(class_='tag')
            if tags_elem:
                description = " ".join([tag.get_text(strip=True) for tag in tags_elem])
            
            # Also get any other text content
            desc_elem = element.find(['p', 'div'], class_=re.compile(r'description|summary', re.I))
            if desc_elem:
                description += " " + desc_elem.get_text(strip=True)
            
            if not description:
                description = element.get_text(strip=True, separator=' ')[:500]
            
            priority, reason = JobRanker.rank_job(title, description)
            
            if priority == JobPriority.BLACKLISTED:
                return None
            
            return Job(
                title=title,
                company=company,
                url=url,
                description=description[:300],
                source=self.source_name,
                priority=priority,
                priority_reason=reason
            )
        except Exception as e:
            logger.warning(f"Error parsing job: {e}")
            return None


class WellfoundScraper(JobScraper):
    """Scraper for Wellfound (formerly AngelList) - startup jobs"""
    
    def __init__(self):
        super().__init__(
            "Wellfound",
            "https://wellfound.com",
            job_list_selector='div[class*="job"], div[class*="JobListing"]',
            wait_timeout=30000
        )
        # Use crypto/web3 role search
        self.search_url = "https://wellfound.com/role/r/web3-engineer"

    async def scrape(self) -> List[Job]:
        """Scrape Wellfound crypto/web3 jobs"""
        jobs = []
        page = None
        try:
            logger.info(f"Scraping {self.source_name}...")
            
            page = await PlaywrightBrowserManager.create_page()
            await page.goto(self.search_url, wait_until='domcontentloaded', timeout=30000)
            
            # Wait for job listings to load
            try:
                await page.wait_for_selector('div[class*="JobListing"], div[class*="job"], article', timeout=10000)
            except PlaywrightTimeoutError:
                logger.warning(f"Timeout waiting for job listings on {self.source_name}")
            
            await page.wait_for_timeout(3000)
            
            # Handle scroll
            await self.handle_infinite_scroll(page)
            
            content = await page.content()
            
            if not content:
                logger.warning(f"No content retrieved from {self.source_name}")
                return jobs
            
            soup = BeautifulSoup(content, 'html.parser')
            
            # Wellfound uses divs with job-related classes
            job_elements = []
            
            # Strategy 1: Divs with JobListing classes
            job_elements = soup.find_all('div', class_=re.compile(r'JobListing|job-listing', re.I))
            
            # Strategy 2: Articles
            if not job_elements:
                job_elements = soup.find_all('article')
            
            # Strategy 3: Divs with job/card classes
            if not job_elements:
                job_elements = soup.find_all('div', class_=re.compile(r'job|card|position', re.I))
            
            # Strategy 4: Links to job pages
            if not job_elements:
                job_elements = soup.find_all('a', href=re.compile(r'/jobs/|/l/'))
            
            logger.info(f"Found {len(job_elements)} potential job elements from {self.source_name}")
            
            if not job_elements:
                logger.warning(f"No job elements found on {self.source_name}")
                screenshot_path = f"debug_Wellfound.png"
                await page.screenshot(path=screenshot_path, full_page=True)
                logger.info(f"Debug screenshot saved to {screenshot_path}")
            
            parsed_count = 0
            for element in job_elements[:100]:
                try:
                    job = self.parse_job(element)
                    if job:
                        jobs.append(job)
                        parsed_count += 1
                except Exception as e:
                    logger.warning(f"Error parsing job element: {e}")
                    continue
            logger.info(f"Successfully scraped {parsed_count} jobs from {self.source_name}")
                    
        except Exception as e:
            logger.error(f"Error scraping {self.source_name}: {e}")
        finally:
            if page:
                await page.close()
        
        return jobs

    def parse_job(self, element, job_url: str = None) -> Optional[Job]:
        """Parse a Wellfound job element"""
        try:
            # Extract URL
            url = job_url
            link_elem = element.find('a', href=True)
            if link_elem and link_elem.get('href'):
                href = link_elem['href']
                if not href.startswith('http'):
                    url = f"{self.base_url}{href}"
                else:
                    url = href
            
            if not url:
                # If element itself is a link
                if element.name == 'a' and element.get('href'):
                    href = element.get('href')
                    if not href.startswith('http'):
                        url = f"{self.base_url}{href}"
                    else:
                        url = href
                else:
                    return None
            
            # Extract title
            title = "Unknown Title"
            title_elem = element.find(['h2', 'h3', 'h4', 'span'], class_=re.compile(r'title|role|position', re.I))
            if title_elem:
                title = title_elem.get_text(strip=True)
            elif link_elem:
                title = link_elem.get_text(strip=True)
            
            if not title or len(title) < 5:
                return None
            
            # Skip if it's clearly not a job
            if any(skip in title.lower() for skip in ['sign up', 'log in', 'get started', 'learn more']):
                return None
            
            # Extract company
            company = "Unknown"
            company_elem = element.find(['span', 'div', 'a'], class_=re.compile(r'company|startup', re.I))
            if company_elem:
                company = company_elem.get_text(strip=True)
            
            # Extract description/tags
            description = ""
            desc_elem = element.find(['p', 'div', 'span'], class_=re.compile(r'description|summary|tag', re.I))
            if desc_elem:
                description = desc_elem.get_text(strip=True)
            else:
                description = element.get_text(strip=True, separator=' ')[:500]
            
            priority, reason = JobRanker.rank_job(title, description)
            
            if priority == JobPriority.BLACKLISTED:
                return None
            
            return Job(
                title=title,
                company=company,
                url=url,
                description=description[:300],
                source=self.source_name,
                priority=priority,
                priority_reason=reason
            )
        except Exception as e:
            logger.warning(f"Error parsing job: {e}")
            return None


# ═══════════════════════════════════════════════════════════════════
# General / Remote scrapers — "normal company" division
# ═══════════════════════════════════════════════════════════════════

class WeWorkRemotelyScraper(JobScraper):
    """Scraper for WeWorkRemotely — DevOps / SysAdmin / Automation roles"""

    def __init__(self):
        super().__init__(
            "WeWorkRemotely",
            "https://weworkremotely.com",
            job_list_selector='section.jobs article li',
            wait_timeout=30000,
        )
        self.search_url = "https://weworkremotely.com/categories/remote-devops-sysadmin-jobs"

    async def scrape(self) -> List[Job]:
        jobs: List[Job] = []
        page = None
        try:
            logger.info(f"Scraping {self.source_name}...")
            page = await PlaywrightBrowserManager.create_page()
            await page.goto(self.search_url, wait_until='domcontentloaded', timeout=30000)
            try:
                await page.wait_for_selector('section.jobs li a[href*="/remote-jobs/"]', timeout=10000)
            except PlaywrightTimeoutError:
                logger.warning(f"Timeout waiting for job listings on {self.source_name}")
            await page.wait_for_timeout(2000)
            content = await page.content()
            if not content:
                return jobs

            soup = BeautifulSoup(content, 'html.parser')
            # WWR lists jobs as <li> inside <section class="jobs">
            job_elements = soup.select('section.jobs li a[href*="/remote-jobs/"]')
            if not job_elements:
                job_elements = soup.select('li a[href*="/remote-jobs/"]')

            logger.info(f"Found {len(job_elements)} potential job elements from {self.source_name}")

            for element in job_elements[:80]:
                try:
                    job = self._parse_wwr(element)
                    if job:
                        jobs.append(job)
                except Exception as e:
                    logger.warning(f"Error parsing WWR job: {e}")

            logger.info(f"Successfully scraped {len(jobs)} jobs from {self.source_name}")
        except Exception as e:
            logger.error(f"Error scraping {self.source_name}: {e}")
        finally:
            if page:
                await page.close()
        return jobs

    def _parse_wwr(self, element) -> Optional[Job]:
        href = element.get('href', '')
        if not href or href == '#':
            return None
        url = f"{self.base_url}{href}" if not href.startswith('http') else href

        # Title is usually in <span class="title">
        title_el = element.find('span', class_='title')
        title = title_el.get_text(strip=True) if title_el else element.get_text(strip=True)
        if not title or len(title) < 5:
            return None

        # Company
        company_el = element.find('span', class_='company')
        company = company_el.get_text(strip=True) if company_el else "Unknown"

        # Region / extra info
        region_el = element.find('span', class_='region')
        region = region_el.get_text(strip=True) if region_el else ""

        description = f"{title} {region}"
        priority, reason = JobRanker.rank_job(title, description)
        if priority == JobPriority.BLACKLISTED:
            return None

        return Job(
            title=title, company=company, url=url,
            description=description[:300], source=self.source_name,
            priority=priority, priority_reason=reason, category="general",
        )


class JobicyScraper(JobScraper):
    """Scraper for Jobicy — remote-first job board with a public JSON API."""

    API_URL = "https://jobicy.com/api/v2/remote-jobs?count=50&tag=devops,sysadmin,automation,infrastructure,platform+engineer"

    def __init__(self):
        super().__init__("Jobicy", "https://jobicy.com")

    async def scrape(self) -> List[Job]:
        jobs: List[Job] = []
        try:
            logger.info(f"Scraping {self.source_name} (API)...")
            headers = {
                "User-Agent": random.choice(_USER_AGENTS),
                "Accept": "application/json",
            }
            resp = requests.get(self.API_URL, headers=headers, timeout=20)
            resp.raise_for_status()
            data = resp.json()

            job_list = data.get("jobs", [])
            logger.info(f"Found {len(job_list)} jobs from Jobicy API")

            for entry in job_list:
                try:
                    job = self._parse_entry(entry)
                    if job:
                        jobs.append(job)
                except Exception as e:
                    logger.warning(f"Error parsing Jobicy entry: {e}")

            logger.info(f"Successfully scraped {len(jobs)} jobs from {self.source_name}")
        except Exception as e:
            logger.error(f"Error scraping {self.source_name}: {e}")
        return jobs

    def _parse_entry(self, entry: dict) -> Optional[Job]:
        title = entry.get("jobTitle", "").strip()
        if not title or len(title) < 5:
            return None

        url = entry.get("url", "")
        company = entry.get("companyName", "Unknown")
        location = entry.get("jobGeo", "Remote")
        job_type = entry.get("jobType", "")
        description = f"{title} {location} {job_type}"

        priority, reason = JobRanker.rank_job(title, description)
        if priority == JobPriority.BLACKLISTED:
            return None

        return Job(
            title=title, company=company, url=url,
            description=description[:300], source=self.source_name,
            priority=priority, priority_reason=reason, category="general",
        )


class RemoteOKGeneralScraper(JobScraper):
    """Scraper for RemoteOK — DevOps / SysAdmin / Infra (non-crypto) remote jobs"""

    def __init__(self):
        super().__init__(
            "RemoteOK (DevOps)",
            "https://remoteok.com",
            job_list_selector='tr.job',
            wait_timeout=30000,
        )
        self.search_url = "https://remoteok.com/remote-devops-jobs"

    async def scrape(self) -> List[Job]:
        jobs: List[Job] = []
        page = None
        try:
            logger.info(f"Scraping {self.source_name}...")
            page = await PlaywrightBrowserManager.create_page()
            await page.goto(self.search_url, wait_until='domcontentloaded', timeout=30000)
            try:
                await page.wait_for_selector('tr.job, table.jobs', timeout=10000)
            except PlaywrightTimeoutError:
                logger.warning(f"Timeout waiting for job listings on {self.source_name}")
            await page.wait_for_timeout(2000)
            await self.handle_infinite_scroll(page)

            content = await page.content()
            if not content:
                return jobs

            soup = BeautifulSoup(content, 'html.parser')
            job_elements = soup.find_all('tr', class_='job')
            if not job_elements:
                jobs_table = soup.find('table', class_='jobs')
                if jobs_table:
                    job_elements = jobs_table.find_all('tr')[1:]

            logger.info(f"Found {len(job_elements)} potential job elements from {self.source_name}")

            for element in job_elements[:100]:
                try:
                    job = self._parse_rok(element)
                    if job:
                        jobs.append(job)
                except Exception as e:
                    logger.warning(f"Error parsing RemoteOK job: {e}")

            logger.info(f"Successfully scraped {len(jobs)} jobs from {self.source_name}")
        except Exception as e:
            logger.error(f"Error scraping {self.source_name}: {e}")
        finally:
            if page:
                await page.close()
        return jobs

    def _parse_rok(self, element) -> Optional[Job]:
        if element.get('class') and any(cls in ['header', 'ad', 'separator'] for cls in element.get('class', [])):
            return None

        url = None
        link_elem = element.find('a', href=True, itemprop='url')
        if not link_elem:
            link_elem = element.find('a', href=True)
        if link_elem and link_elem.get('href'):
            href = link_elem['href']
            url = f"{self.base_url}{href}" if not href.startswith('http') else href
        if not url:
            return None

        title = "Unknown Title"
        title_elem = element.find('h2', itemprop='title')
        if not title_elem:
            title_elem = element.find(['h2', 'h3', 'td'], class_=re.compile(r'title|position', re.I))
        if title_elem:
            title = title_elem.get_text(strip=True)
        if not title or len(title) < 5:
            return None

        company = "Unknown"
        company_elem = element.find('h3', itemprop='name')
        if not company_elem:
            company_elem = element.find(['h3', 'span', 'td'], class_=re.compile(r'company|employer', re.I))
        if company_elem:
            company = company_elem.get_text(strip=True)

        description = ""
        tags_elem = element.find_all(class_='tag')
        if tags_elem:
            description = " ".join([tag.get_text(strip=True) for tag in tags_elem])
        desc_elem = element.find(['p', 'div'], class_=re.compile(r'description|summary', re.I))
        if desc_elem:
            description += " " + desc_elem.get_text(strip=True)
        if not description:
            description = element.get_text(strip=True, separator=' ')[:500]

        priority, reason = JobRanker.rank_job(title, description)
        if priority == JobPriority.BLACKLISTED:
            return None

        return Job(
            title=title, company=company, url=url,
            description=description[:300], source=self.source_name,
            priority=priority, priority_reason=reason, category="general",
        )


class TelegramScraper(JobScraper):
    """Scraper for Telegram channels using web preview"""

    def __init__(self):
        super().__init__(
            "Telegram Channels",
            "https://t.me",
            job_list_selector='div.tgme_widget_message_wrap',
            wait_timeout=30000
        )
        # List of Telegram channels to scrape
        self.telegram_channels = [
            "https://t.me/s/job_crypto_eu",
            "https://t.me/s/web3hiring",
            "https://t.me/s/degencryptojobs",
            "https://t.me/s/cryptojobslist"
        ]

    async def scrape(self) -> List[Job]:
        """Scrape all Telegram channels"""
        all_jobs = []
        
        for channel_url in self.telegram_channels:
            jobs = []
            page = None
            channel_name = channel_url.split('/')[-1]
            try:
                logger.info(f"Scraping Telegram channel: {channel_name}...")
                # Wait for message container to load
                page = await PlaywrightBrowserManager.create_page()
                logger.info(f"Loading {channel_url}...")
                
                await page.goto(channel_url, wait_until='domcontentloaded', timeout=self.wait_timeout)
                
                # Wait for message containers to load
                try:
                    await page.wait_for_selector('div.tgme_widget_message_wrap', timeout=10000)
                    logger.info(f"Telegram messages loaded for {channel_name}")
                except PlaywrightTimeoutError:
                    logger.warning(f"Message containers not found for {channel_name}, continuing anyway...")
                
                # Wait a bit more for content to render
                await page.wait_for_timeout(2000)
                
                content = await page.content()
                
                if not content:
                    continue
                
                soup = BeautifulSoup(content, 'html.parser')
                
                # Find message containers
                message_wraps = soup.find_all('div', class_='tgme_widget_message_wrap')
                
                logger.info(f"Found {len(message_wraps)} messages from {channel_name}")
                
                # Get last 15 messages (increased from 10 for better coverage)
                for message_wrap in message_wraps[-15:]:
                    try:
                        job = self.parse_job(message_wrap, channel_name)
                        if job:
                            jobs.append(job)
                            logger.debug(f"Parsed Telegram job: {job.title[:50]}...")
                    except Exception as e:
                        logger.warning(f"Error parsing Telegram message: {e}")
                        continue
                
                all_jobs.extend(jobs)
                if len(jobs) > 0:
                    logger.info(f"Scraped {len(jobs)} jobs from {channel_name}")
                else:
                    logger.info(f"No jobs found in {channel_name} (checked {len(message_wraps)} messages)")
                        
            except Exception as e:
                logger.error(f"Error scraping Telegram channel {channel_name}: {e}")
            finally:
                if page:
                    await page.close()
        
        return all_jobs

    def parse_job(self, element, channel_name: str = None) -> Optional[Job]:
        """Parse a Telegram message element"""
        try:
            # Extract message text
            message_text_elem = element.find('div', class_='tgme_widget_message_text')
            if not message_text_elem:
                return None
            
            message_text = message_text_elem.get_text(strip=True)
            if not message_text or len(message_text) < 10:
                return None
            
            # Look for job keywords (more flexible matching)
            text_lower = message_text.lower()
            job_keywords = [
                'hiring', 'role:', 'salary', 'position', 'job', 'looking for',
                'we\'re hiring', 'we are hiring', 'join us', 'opportunity',
                'open position', 'vacancy', 'recruiting', 'apply now',
                'remote', 'full-time', 'part-time', 'contract', 'freelance'
            ]
            # Check if message contains job-related keywords
            if not any(keyword in text_lower for keyword in job_keywords):
                # Also check if message is long enough and contains common job-related terms
                if len(message_text) < 50 or not any(term in text_lower for term in ['engineer', 'developer', 'manager', 'analyst', 'support', 'devops', 'blockchain', 'crypto', 'web3']):
                    return None
            
            # Extract title (first line or first sentence)
            lines = message_text.split('\n')
            title = lines[0].strip() if lines else message_text[:100].strip()
            if len(title) > 150:
                title = title[:150] + "..."
            
            # Extract URL from message date link
            date_link = element.find('a', class_='tgme_widget_message_date')
            if date_link and date_link.get('href'):
                url = date_link['href']
            else:
                # Fallback: construct URL from channel
                url = f"https://t.me/{channel_name}" if channel_name else "https://t.me/job_crypto_eu"
            
            # Extract company (try to find in text, or use channel name)
            company = f"Telegram ({channel_name})" if channel_name else "Telegram Channel"
            # Look for company mentions in common patterns
            for line in lines[:3]:
                if '@' in line or 'company:' in line.lower() or 'at ' in line.lower():
                    # Try to extract company name
                    parts = line.split('@')
                    if len(parts) > 1:
                        company = parts[1].split()[0] if parts[1].split() else company
                    break
            
            # Use full message as description
            description = message_text[:500]
            
            # Rank the job
            priority, reason = JobRanker.rank_job(title, description)
            
            if priority == JobPriority.BLACKLISTED:
                return None
            
            return Job(
                title=title,
                company=company,
                url=url,
                description=description[:300],
                source=f"Telegram ({channel_name})" if channel_name else self.source_name,
                priority=priority,
                priority_reason=reason
            )
        except Exception as e:
            logger.warning(f"Error parsing Telegram message: {e}")
            return None


# ---------- Cruise / Maritime IT Job Scrapers ----------

# Carnival ship jobs: filter for IT-related roles only
_CARNIVAL_IT_PATTERNS = [
    "it officer", "assistant it", "it systems", "2nd eto", "3rd eto",
    "electro-technical", "information technology", "it administrator",
    "it manager", "it support", "it assistant", "eto officer"
]


class CarnivalShipJobsScraper(JobScraper):
    """Scraper for Carnival Cruise Line ship jobs (IT-related only)."""
    base = "https://shipjobs.carnival.com"

    def __init__(self):
        super().__init__(
            "Carnival Ship Jobs",
            self.base,
            job_list_selector='a[href*="/job/"]',
            wait_timeout=30000
        )
        self.search_url = "https://shipjobs.carnival.com/search?q="

    async def scrape(self) -> List[Job]:
        jobs = []
        page = None
        try:
            logger.info(f"Scraping {self.source_name}...")
            content, page = await self.get_page_content(
                self.search_url, handle_scroll=True
            )
            if not content:
                return jobs
            soup = BeautifulSoup(content, 'html.parser')
            # Links to job pages: /job/slug/id
            for a in soup.find_all('a', href=re.compile(r'/job/[^/]+/\d+')):
                href = a.get('href')
                if not href:
                    continue
                url = href if href.startswith('http') else self.base.rstrip('/') + href
                title = (a.get_text(strip=True) or "").strip()
                if not title or len(title) < 3:
                    continue
                # Find block: often department + link + description
                block = a.find_parent(['div', 'section', 'article', 'li']) or a
                desc = ""
                if block:
                    desc = block.get_text(strip=True, separator=' ')[:500]
                combined = (title + " " + desc).lower()
                if not any(p in combined for p in _CARNIVAL_IT_PATTERNS):
                    continue
                priority, reason = CruiseJobRanker.rank_job(title, desc)
                if priority == JobPriority.BLACKLISTED:
                    continue
                # Company is Carnival
                job = Job(
                    title=title,
                    company="Carnival Cruise Line",
                    url=url,
                    description=desc[:300],
                    source=self.source_name,
                    priority=priority,
                    priority_reason=reason,
                    category="cruise"
                )
                jobs.append(job)
            logger.info(f"Scraped {len(jobs)} IT-related jobs from {self.source_name}")
        except Exception as e:
            logger.error(f"Error scraping {self.source_name}: {e}")
        finally:
            if page:
                await page.close()
        return jobs


class AllCruiseJobsScraper(JobScraper):
    """Scraper for AllCruiseJobs.com IT jobs page."""
    base = "https://www.allcruisejobs.com"

    def __init__(self):
        super().__init__(
            "AllCruiseJobs.com",
            self.base,
            job_list_selector='article, .job, h2',
            wait_timeout=30000
        )
        self.search_url = "https://www.allcruisejobs.com/it-jobs/"

    async def scrape(self) -> List[Job]:
        jobs = []
        page = None
        try:
            logger.info(f"Scraping {self.source_name}...")
            page = await PlaywrightBrowserManager.create_page()
            for page_num in range(1, 3):
                url = f"{self.search_url}{page_num}/" if page_num > 1 else self.search_url
                await page.goto(url, wait_until='domcontentloaded', timeout=self.wait_timeout)
                await page.wait_for_timeout(2000)
                content = await page.content()
                if not content:
                    break
                soup = BeautifulSoup(content, 'html.parser')
                # Common patterns: h2 title, then description, then "Date - Company - Lang"
                for h2 in soup.find_all(['h2', 'h3']):
                    title = h2.get_text(strip=True)
                    if not title or len(title) < 5:
                        continue
                    # Skip non-job headings
                    if title.lower() in ('it jobs on cruise ships', 'cruise ship jobs', 'select a position >'):
                        continue
                    next_el = h2.find_next_sibling()
                    desc = ""
                    company = "Cruise Line"
                    while next_el and next_el.name not in ('h2', 'h3'):
                        text = next_el.get_text(strip=True)
                        if text:
                            if re.match(r'^[A-Za-z]+\s+\d{1,2},?\s+\d{4}\s+-', text):
                                parts = text.split(' - ', 2)
                                if len(parts) >= 2:
                                    company = parts[1].strip()
                            else:
                                desc = text[:400]
                        next_el = next_el.find_next_sibling()
                    link = h2.find_next('a', href=True)
                    url = link['href'] if link and link.get('href') else self.search_url
                    if not url.startswith('http'):
                        url = self.base + url
                    priority, reason = CruiseJobRanker.rank_job(title, desc or title)
                    if priority == JobPriority.BLACKLISTED:
                        continue
                    jobs.append(Job(
                        title=title,
                        company=company,
                        url=url,
                        description=(desc or title)[:300],
                        source=self.source_name,
                        priority=priority,
                        priority_reason=reason,
                        category="cruise"
                    ))
            logger.info(f"Scraped {len(jobs)} jobs from {self.source_name}")
        except Exception as e:
            logger.error(f"Error scraping {self.source_name}: {e}")
        finally:
            if page:
                await page.close()
        return jobs


class SelectionPartnersScraper(JobScraper):
    """Scraper for Selection Partners cruise jobs (IT positions only)."""
    base = "https://selectionpartners.net"

    def __init__(self):
        super().__init__(
            "Selection Partners",
            self.base,
            job_list_selector='article, .job, h3',
            wait_timeout=30000
        )
        self.search_url = "https://selectionpartners.net/jobs/"

    async def scrape(self) -> List[Job]:
        jobs = []
        page = None
        try:
            logger.info(f"Scraping {self.source_name}...")
            content, page = await self.get_page_content(self.search_url, handle_scroll=True)
            if not content:
                return jobs
            soup = BeautifulSoup(content, 'html.parser')
            for h3 in soup.find_all('h3'):
                title = h3.get_text(strip=True)
                if not title or 'it' not in title.lower():
                    continue
                link = h3.find_next('a', href=True) or h3.find_previous('a', href=True)
                if not link or not link.get('href'):
                    continue
                href = link['href']
                url = href if href.startswith('http') else self.base.rstrip('/') + '/' + href.lstrip('/')
                # Company from link text (e.g. "10 paísesPrincess CruisesAbierta" -> Princess Cruises)
                link_text = link.get_text(strip=True) or ""
                company = "Cruise Line"
                for cruise in ("Princess Cruises", "Royal Caribbean", "Celebrity", "Carnival Cruise Line", "Seabourn", "P&O Cruises"):
                    if cruise.lower() in link_text.lower():
                        company = cruise
                        break
                priority, reason = CruiseJobRanker.rank_job(title, title)
                if priority == JobPriority.BLACKLISTED:
                    continue
                jobs.append(Job(
                    title=title,
                    company=company,
                    url=url,
                    description=title[:300],
                    source=self.source_name,
                    priority=priority,
                    priority_reason=reason,
                    category="cruise"
                ))
            logger.info(f"Scraped {len(jobs)} IT jobs from {self.source_name}")
        except Exception as e:
            logger.error(f"Error scraping {self.source_name}: {e}")
        finally:
            if page:
                await page.close()
        return jobs


class PeopleConquestScraper(JobScraper):
    """Scraper for PeopleConquest jobs (Informática / IT)."""
    base = "https://www.peopleconquest.com"

    def __init__(self):
        super().__init__(
            "PeopleConquest",
            self.base,
            job_list_selector='a[href*="job"], .job',
            wait_timeout=30000
        )
        self.search_url = "https://www.peopleconquest.com/jobs/"

    async def scrape(self) -> List[Job]:
        jobs = []
        page = None
        try:
            logger.info(f"Scraping {self.source_name}...")
            content, page = await self.get_page_content(self.search_url)
            if not content:
                return jobs
            soup = BeautifulSoup(content, 'html.parser')
            # Look for job links (structure may vary)
            for a in soup.find_all('a', href=re.compile(r'/job|/jobs/|/oportunidades|/oferta')):
                href = a.get('href', '')
                if not href or '#' in href:
                    continue
                text = a.get_text(strip=True)
                if not text or len(text) < 5 or len(text) > 200:
                    continue
                url = href if href.startswith('http') else self.base + href
                # Prefer IT-related if we can detect
                combined = (text + " " + href).lower()
                if 'informática' in combined or 'it ' in combined or 'technology' in combined or 'tech' in combined:
                    priority, reason = CruiseJobRanker.rank_job(text, text)
                else:
                    priority = JobPriority.WEAK_MATCH
                    reason = "Cruise/maritime job board"
                jobs.append(Job(
                    title=text,
                    company="PeopleConquest",
                    url=url,
                    description=text[:300],
                    source=self.source_name,
                    priority=priority,
                    priority_reason=reason,
                    category="cruise"
                ))
            logger.info(f"Scraped {len(jobs)} jobs from {self.source_name}")
        except Exception as e:
            logger.error(f"Error scraping {self.source_name}: {e}")
        finally:
            if page:
                await page.close()
        return jobs


class DouroAzulScraper(JobScraper):
    """Scraper for Douro Azul opportunities (IT filter)."""
    base = "https://www.douroazul.com"

    def __init__(self):
        super().__init__(
            "Douro Azul",
            self.base,
            job_list_selector='.job, article, .result',
            wait_timeout=30000
        )
        self.search_url = "https://www.douroazul.com/oportunidades/?_sft_area-funcao=information-technology"

    async def scrape(self) -> List[Job]:
        jobs = []
        page = None
        try:
            logger.info(f"Scraping {self.source_name}...")
            content, page = await self.get_page_content(self.search_url)
            if not content:
                return jobs
            if 'não foram encontrados resultados' in content.lower() or 'no results' in content.lower():
                logger.info(f"No IT vacancies at {self.source_name}")
                return jobs
            soup = BeautifulSoup(content, 'html.parser')
            for a in soup.find_all('a', href=re.compile(r'/oportunidades/|/job|smartrecruiters')):
                href = a.get('href', '')
                if not href:
                    continue
                text = a.get_text(strip=True)
                if not text or len(text) < 5:
                    continue
                url = href if href.startswith('http') else self.base + href
                priority, reason = CruiseJobRanker.rank_job(text, text)
                if priority == JobPriority.BLACKLISTED:
                    continue
                jobs.append(Job(
                    title=text,
                    company="Douro Azul",
                    url=url,
                    description=text[:300],
                    source=self.source_name,
                    priority=priority,
                    priority_reason=reason,
                    category="cruise"
                ))
            logger.info(f"Scraped {len(jobs)} jobs from {self.source_name}")
        except Exception as e:
            logger.error(f"Error scraping {self.source_name}: {e}")
        finally:
            if page:
                await page.close()
        return jobs


class DiscordNotifier:
    """Send job summaries to Discord via webhook"""

    # Seniority tiers — lower number = shown first
    _SENIOR_KEYWORDS = [
        "senior", "sr.", "sr ", "lead", "staff", "principal", "head of",
        "director", "vp ", "vice president", "chief",
    ]
    _JUNIOR_KEYWORDS = [
        "junior", "jr.", "jr ", "intern", "entry", "associate", "trainee",
        "apprentice", "graduate",
    ]

    # Discord embed colours
    _CLR_PERFECT  = 0x57F287   # bright green
    _CLR_GOOD     = 0x5865F2   # blurple
    _CLR_TELEGRAM = 0x0088CC   # telegram-blue
    _CLR_WEAK     = 0x95A5A6   # grey
    _CLR_GENERAL  = 0xEB459E   # fuchsia — distinguishes general division

    # Category labels & icons
    _CAT_META = {
        "crypto":  ("🪙", "Crypto / Web3"),
        "cruise":  ("🚢", "Cruise / Maritime IT"),
        "general": ("💼", "General / Remote"),
    }

    def __init__(self, webhook_url: str):
        self.webhook_url = webhook_url

    # ── helpers ───────────────────────────────────────────────────────

    @staticmethod
    def _seniority_tier(title: str) -> int:
        """0 = junior/entry (show first), 1 = mid/unspecified, 2 = senior+."""
        t = title.lower()
        if any(kw in t for kw in DiscordNotifier._JUNIOR_KEYWORDS):
            return 0
        if any(kw in t for kw in DiscordNotifier._SENIOR_KEYWORDS):
            return 2
        return 1

    @staticmethod
    def _sort_by_seniority(jobs: List[Job]) -> List[Job]:
        return sorted(jobs, key=lambda j: DiscordNotifier._seniority_tier(j.title))

    @staticmethod
    def _seniority_dot(title: str) -> str:
        """Single coloured dot for seniority."""
        tier = DiscordNotifier._seniority_tier(title)
        return "🟢" if tier == 0 else ("🔴" if tier == 2 else "🟡")

    @staticmethod
    def _seniority_label(title: str) -> str:
        tier = DiscordNotifier._seniority_tier(title)
        if tier == 0:
            return "Junior / Entry"
        if tier == 2:
            return "Senior+"
        return "Mid-Level"

    @staticmethod
    def _embed_char_count(embed: dict) -> int:
        """Count characters in an embed for Discord's 6000-char total limit."""
        count = len(embed.get("title", ""))
        count += len(embed.get("description", ""))
        if "footer" in embed:
            count += len(embed["footer"].get("text", ""))
        if "author" in embed:
            count += len(embed["author"].get("name", ""))
        for field in embed.get("fields", []):
            count += len(field.get("name", ""))
            count += len(field.get("value", ""))
        return count

    def _send_embeds_batched(self, embeds: List[dict]):
        """Send embeds in batches respecting Discord's 6000-char and 10-embed limits."""
        CHAR_LIMIT = 5800  # buffer below Discord's 6000
        batch: List[dict] = []
        batch_chars = 0
        for embed in embeds:
            ec = self._embed_char_count(embed)
            if batch and (batch_chars + ec > CHAR_LIMIT or len(batch) >= 10):
                self._send_payload({"content": "", "embeds": batch})
                time.sleep(0.5)
                batch = []
                batch_chars = 0
            batch.append(embed)
            batch_chars += ec
        if batch:
            self._send_payload({"content": "", "embeds": batch})
            time.sleep(0.5)

    def _send_payload(self, payload: dict):
        """Send a single payload to Discord with rate-limit awareness."""
        try:
            response = requests.post(self.webhook_url, json=payload, timeout=30)
            if response.status_code == 429:
                retry_after = response.json().get("retry_after", 2)
                logger.warning(f"Discord rate limited — waiting {retry_after}s")
                time.sleep(retry_after)
                response = requests.post(self.webhook_url, json=payload, timeout=30)
            response.raise_for_status()
            return response
        except requests.exceptions.RequestException as e:
            logger.error(f"Error sending to Discord: {e}")
            if hasattr(e, 'response') and e.response is not None:
                logger.error(f"Discord API: {e.response.status_code} - {e.response.text[:500]}")
            raise

    # ── embed builders ────────────────────────────────────────────────

    def _build_job_field(self, job: Job) -> dict:
        """Compact 2-line embed field for a job."""
        title = job.title[:70] + "..." if len(job.title) > 70 else job.title
        dot = self._seniority_dot(job.title)
        level = self._seniority_label(job.title)
        company = job.company if job.company != "Unknown" else "—"
        return {
            "name": f"{dot} {title}",
            "value": (
                f"🏢 {company}  ·  {dot} {level}\n"
                f"🔗 [Apply / View Job]({job.url})"
            ),
            "inline": False,
        }

    def _build_telegram_field(self, job: Job) -> dict:
        """Compact field for a Telegram job."""
        title = job.title[:85] + "..." if len(job.title) > 85 else job.title
        channel = job.source.replace("Telegram (", "").rstrip(")")
        return {
            "name": title,
            "value": f"📱 `#{channel}` · [View Post]({job.url})",
            "inline": False,
        }

    def _make_embeds(self, jobs: List[Job], title: str, color: int,
                     is_telegram: bool = False, max_per_embed: int = 8) -> List[dict]:
        """Build list of embeds from a job list, chunked to fit Discord limits."""
        if not jobs:
            return []
        chunks = [jobs[i:i + max_per_embed] for i in range(0, len(jobs), max_per_embed)]
        embeds = []
        for i, chunk in enumerate(chunks):
            part = f" ({i + 1}/{len(chunks)})" if len(chunks) > 1 else ""
            embed = {"title": f"{title}{part}", "color": color, "fields": []}
            for job in chunk:
                if is_telegram:
                    embed["fields"].append(self._build_telegram_field(job))
                else:
                    embed["fields"].append(self._build_job_field(job))
            embed["footer"] = {"text": f"{len(jobs)} job(s) in this section"}
            embeds.append(embed)
        return embeds

    # ── per-category helpers ──────────────────────────────────────────

    def _split_category(self, jobs: List[Job]) -> dict:
        """Split jobs for a single category into priority buckets."""
        telegram = [j for j in jobs if 'Telegram' in j.source]
        other = [j for j in jobs if 'Telegram' not in j.source]
        return {
            "perfect": self._sort_by_seniority([j for j in other if j.priority == JobPriority.PERFECT_MATCH]),
            "good":    self._sort_by_seniority([j for j in other if j.priority == JobPriority.GOOD_MATCH]),
            "weak":    [j for j in other if j.priority == JobPriority.WEAK_MATCH],
            "telegram": telegram,
        }

    def _stat_line(self, icon: str, label: str, buckets: dict) -> str:
        """Build one stat line for the header."""
        p, g, w, t = len(buckets["perfect"]), len(buckets["good"]), len(buckets["weak"]), len(buckets["telegram"])
        total = p + g + w + t
        line = f"{icon} **{label}** — 🥇 {p} · 🥈 {g} · 🔍 {w}"
        if t:
            line += f" · 📱 {t}"
        line += f" · **{total} total**"
        return line

    def _build_category_embeds(self, cat_key: str, buckets: dict) -> List[dict]:
        """Build all embeds for one category (perfect + good + telegram)."""
        icon, label = self._CAT_META.get(cat_key, ("📌", cat_key.title()))
        color_perfect = self._CLR_PERFECT
        color_good = self._CLR_GENERAL if cat_key == "general" else self._CLR_GOOD
        embeds = []
        embeds.extend(self._make_embeds(
            buckets["perfect"], f"{icon}  {label} — 🥇 Perfect Matches", color_perfect))
        embeds.extend(self._make_embeds(
            buckets["good"], f"{icon}  {label} — 🥈 Good Matches", color_good))
        if buckets["telegram"]:
            embeds.extend(self._make_embeds(
                buckets["telegram"], f"{icon}  {label} — 📱 Telegram Finds",
                self._CLR_TELEGRAM, is_telegram=True, max_per_embed=10))
        return embeds

    # ── main send ─────────────────────────────────────────────────────

    def send_summary(self, jobs: List[Job], include_all_weak_matches: bool = False):
        """Send formatted job summary to Discord.

        Layout per division:
        1. Header message with stats for all divisions
        2. Per-division embeds: Perfect > Good > Telegram, sorted by seniority
        3. Combined weak matches as compact text
        """
        if not jobs:
            logger.info("No jobs to send to Discord")
            return

        # ── split by category ──
        cats = {}
        for cat_key in ("crypto", "cruise", "general"):
            cat_jobs = sorted(
                [j for j in jobs if _job_category(j) == cat_key],
                key=lambda x: x.priority.value,
            )
            cats[cat_key] = self._split_category(cat_jobs)

        # Gather all weak matches across divisions
        weak_matches = []
        for buckets in cats.values():
            weak_matches.extend(buckets["weak"])
        remaining_weak_matches = []

        # ── 1. Header ──
        now_str = datetime.now().strftime("%A, %d %B %Y · %H:%M")
        header = f"# 📋 Daily Job Report\n"
        header += f"> {now_str}  ·  Bot **{BOT_VERSION}**\n\n"
        for cat_key in ("crypto", "cruise", "general"):
            icon, label = self._CAT_META[cat_key]
            header += self._stat_line(icon, label, cats[cat_key]) + "\n"

        self._send_payload({"content": header.rstrip(), "embeds": []})
        time.sleep(0.5)

        # ── 2. Embeds per division ──
        all_embeds = []
        for cat_key in ("crypto", "cruise", "general"):
            all_embeds.extend(self._build_category_embeds(cat_key, cats[cat_key]))

        # Send embeds in batches respecting Discord's 6000-char limit
        self._send_embeds_batched(all_embeds)

        # ── 3. Weak matches as compact text ──
        if weak_matches:
            weak_text = "## 🔍 Other Potential Roles\n"
            shown_count = 0
            for job in weak_matches:
                title = job.title[:70] + "..." if len(job.title) > 70 else job.title
                company = job.company[:25] + "..." if len(job.company) > 25 else job.company
                dot = self._seniority_dot(job.title)
                cat_icon = self._CAT_META.get(_job_category(job), ("📌",))[0]
                line = f"{dot} **{title}** @ {company} {cat_icon} — [View]({job.url})\n"
                if len(weak_text) + len(line) > 1850:
                    remaining_weak_matches = weak_matches[shown_count:]
                    remaining = len(remaining_weak_matches)
                    if include_all_weak_matches:
                        weak_text += f"\n*… and {remaining} more (continued below)*"
                    else:
                        weak_text += f"\n*… and {remaining} more (sent at follow-up)*"
                    break
                weak_text += line
                shown_count += 1

            self._send_payload({"content": weak_text, "embeds": []})
            time.sleep(0.5)

            if not include_all_weak_matches and remaining_weak_matches:
                save_remaining_weak_matches(remaining_weak_matches)

        # ── 4. Overflow weak matches (startup run) ──
        if include_all_weak_matches and remaining_weak_matches:
            logger.info(f"Sending {len(remaining_weak_matches)} additional weak matches...")
            chunks_text = []
            current = "## 🔍 Other Potential Roles (continued)\n"
            for job in remaining_weak_matches:
                title = job.title[:70] + "..." if len(job.title) > 70 else job.title
                company = job.company[:25] + "..." if len(job.company) > 25 else job.company
                dot = self._seniority_dot(job.title)
                cat_icon = self._CAT_META.get(_job_category(job), ("📌",))[0]
                line = f"{dot} **{title}** @ {company} {cat_icon} — [View]({job.url})\n"
                if len(current) + len(line) > 1900:
                    chunks_text.append(current)
                    current = "## 🔍 Other Potential Roles (continued)\n" + line
                else:
                    current += line
            if current.strip():
                chunks_text.append(current)
            for chunk_text in chunks_text:
                self._send_payload({"content": chunk_text, "embeds": []})
                time.sleep(0.5)
        elif not include_all_weak_matches and remaining_weak_matches:
            logger.info(f"Saved {len(remaining_weak_matches)} remaining weak matches for follow-up")

        logger.info(f"Discord report sent — {len(jobs)} jobs total")


async def _scrape_single(scraper: JobScraper) -> List[Job]:
    """Run a single scraper with error handling. Returns jobs or empty list on failure."""
    try:
        jobs = await scraper.scrape()
        logger.info(f"Scraped {len(jobs)} jobs from {scraper.source_name}")
        return jobs
    except Exception as e:
        logger.error(f"Failed to scrape {scraper.source_name}: {e}")
        return []


async def scrape_all_jobs() -> List[Job]:
    """Scrape all job sources concurrently for maximum speed"""
    scrapers = [
        # Crypto / Web3
        Web3CareerScraper(),
        CryptoJobsListScraper(),
        CryptocurrencyJobsScraper(),
        CryptoJobsScraper(),
        FindCryptoJobsScraper(),
        BondexScraper(),
        RemoteOKScraper(),
        WellfoundScraper(),
        SolanaJobsScraper(),
        DelphiVenturesScraper(),
        TelegramScraper(),
        # Cruise / Maritime IT
        CarnivalShipJobsScraper(),
        AllCruiseJobsScraper(),
        SelectionPartnersScraper(),
        PeopleConquestScraper(),
        DouroAzulScraper(),
        # General / Remote
        WeWorkRemotelyScraper(),
        JobicyScraper(),
        RemoteOKGeneralScraper(),
    ]

    # Run all scrapers concurrently (massive speed improvement)
    # Each scraper has its own error handling so one failure won't affect others
    logger.info(f"Launching {len(scrapers)} scrapers concurrently...")
    results = await asyncio.gather(*[_scrape_single(s) for s in scrapers])

    all_jobs = []
    for jobs in results:
        all_jobs.extend(jobs)

    return all_jobs


def normalize_url(url: str) -> str:
    """Normalize URL for deduplication (remove query params, fragments, trailing slashes)"""
    try:
        parsed = urlparse(url)
        # Remove query params and fragments
        normalized = urlunparse((
            parsed.scheme,
            parsed.netloc,
            parsed.path.rstrip('/'),  # Remove trailing slash
            '',  # params
            '',  # query
            ''   # fragment
        ))
        return normalized.lower()
    except Exception:
        return url.lower()


def normalize_title(title: str) -> str:
    """Normalize job title for deduplication"""
    # Remove extra whitespace, convert to lowercase, remove special chars
    normalized = re.sub(r'\s+', ' ', title.lower().strip())
    # Remove common variations
    normalized = re.sub(r'\s*@\s*unknown\s*', '', normalized)
    normalized = re.sub(r'\s*\(.*?\)\s*', '', normalized)  # Remove parentheses content
    normalized = re.sub(r'[^\w\s]', '', normalized)  # Remove special chars
    return normalized.strip()


def load_seen_jobs() -> Tuple[Dict[str, str], Dict[str, str]]:
    """Load seen job URLs and titles from file.

    Returns:
        tuple: (seen_urls_dict {normalized_url: date_str}, seen_titles_dict {normalized_title: date_str})
    """
    seen_jobs_file = 'seen_jobs.json'
    seen_urls: Dict[str, str] = {}
    seen_titles: Dict[str, str] = {}
    today = datetime.now().strftime("%Y-%m-%d")

    try:
        if os.path.exists(seen_jobs_file):
            with open(seen_jobs_file, 'r') as f:
                data = json.load(f)

                # Handle legacy formats
                if isinstance(data, list):
                    # Old format v1: just a list of URLs
                    seen_urls = {normalize_url(url): today for url in data}
                elif isinstance(data, dict):
                    raw_urls = data.get('urls', [])
                    raw_titles = data.get('titles', [])
                    # v3 format: urls/titles are dicts {value: date}
                    if isinstance(raw_urls, dict):
                        seen_urls = {normalize_url(u): d for u, d in raw_urls.items()}
                    else:
                        # v2 format: urls/titles are plain lists
                        seen_urls = {normalize_url(u): today for u in raw_urls}
                    if isinstance(raw_titles, dict):
                        seen_titles = {normalize_title(t): d for t, d in raw_titles.items()}
                    else:
                        seen_titles = {normalize_title(t): today for t in raw_titles}

                logger.info(f"Loaded {len(seen_urls)} seen URLs and {len(seen_titles)} seen titles from memory")
    except Exception as e:
        logger.warning(f"Error loading seen_jobs.json: {e}")

    return seen_urls, seen_titles


def save_remaining_weak_matches(remaining_jobs: List[Job]):
    """Save remaining weak matches to file for 9:05 AM message"""
    remaining_file = 'remaining_weak_matches.json'
    try:
        jobs_data = [job.to_dict() for job in remaining_jobs]
        with open(remaining_file, 'w') as f:
            json.dump(jobs_data, f, indent=2)
        logger.info(f"Saved {len(remaining_jobs)} remaining weak matches to {remaining_file}")
    except Exception as e:
        logger.error(f"Error saving remaining weak matches: {e}")


def load_remaining_weak_matches() -> List[Job]:
    """Load remaining weak matches from file"""
    remaining_file = 'remaining_weak_matches.json'
    jobs = []
    try:
        if os.path.exists(remaining_file):
            with open(remaining_file, 'r') as f:
                jobs_data = json.load(f)
                for job_data in jobs_data:
                    job = Job(
                        title=job_data['title'],
                        company=job_data['company'],
                        url=job_data['url'],
                        description=job_data['description'],
                        source=job_data['source'],
                        priority=JobPriority[job_data['priority']],
                        priority_reason=job_data['priority_reason'],
                        posted_date=job_data.get('posted_date'),
                        category=job_data.get('category', 'crypto')
                    )
                    jobs.append(job)
            logger.info(f"Loaded {len(jobs)} remaining weak matches from {remaining_file}")
    except Exception as e:
        logger.warning(f"Error loading remaining weak matches: {e}")
    return jobs


def send_remaining_weak_matches():
    """Send remaining weak matches at 9:05 AM"""
    webhook_url = os.getenv('DISCORD_WEBHOOK_URL')
    if not webhook_url:
        logger.warning("DISCORD_WEBHOOK_URL not set. Cannot send remaining weak matches.")
        return
    
    remaining_jobs = load_remaining_weak_matches()
    if not remaining_jobs:
        logger.info("No remaining weak matches to send")
        # Send confirmation message to Discord
        try:
            payload = {
                "content": "------\n✅ **9:05 AM Check:** No remaining weak matches to send (all jobs were shown in the 9:00 AM report)",
                "embeds": []
            }
            response = requests.post(webhook_url, json=payload, timeout=30)
            response.raise_for_status()
            logger.info(f"Sent 'no remaining weak matches' confirmation to Discord (HTTP {response.status_code})")
        except Exception as e:
            logger.error(f"Error sending confirmation to Discord: {e}")
        return
    
    try:
        # Build message with divider
        content_text = "------\n"
        content_text += f"**🔍 Remaining Weak Matches ({len(remaining_jobs)} jobs):**\n\n"
        
        # Split into chunks if needed (Discord content limit is 2000 chars)
        chunks = []
        current_chunk = content_text
        
        for job in remaining_jobs:
            title = job.title[:80] + "..." if len(job.title) > 80 else job.title
            company = job.company[:30] + "..." if len(job.company) > 30 else job.company
            line = f"• {title} @ {company} - [View]({job.url})\n"
            
            # If adding this line would exceed limit, start a new chunk
            if len(current_chunk) + len(line) > 1900:
                chunks.append(current_chunk)
                current_chunk = "------\n**🔍 Weak Matches (continued):**\n\n" + line
            else:
                current_chunk += line
        
        # Add the last chunk if it has content
        if current_chunk and len(current_chunk) > len(content_text):
            chunks.append(current_chunk)
        
        # Send each chunk as a separate message
        for chunk_idx, chunk_text in enumerate(chunks):
            try:
                payload = {
                    "content": chunk_text + (f"\n*Part {chunk_idx + 1} of {len(chunks)}*" if len(chunks) > 1 else ""),
                    "embeds": []
                }
                response = requests.post(webhook_url, json=payload, timeout=30)
                response.raise_for_status()
                logger.info(f"Successfully sent remaining weak matches message {chunk_idx + 1}/{len(chunks)} (HTTP {response.status_code})")
                # Small delay between messages
                if chunk_idx < len(chunks) - 1:
                    time.sleep(1)
            except Exception as e:
                logger.error(f"Error sending remaining weak matches message {chunk_idx + 1}: {e}")
        
        # Delete the file after sending
        remaining_file = 'remaining_weak_matches.json'
        try:
            if os.path.exists(remaining_file):
                os.remove(remaining_file)
                logger.info(f"Deleted {remaining_file} after sending")
        except Exception as e:
            logger.warning(f"Error deleting {remaining_file}: {e}")
            
    except Exception as e:
        logger.error(f"Error sending remaining weak matches: {e}")


def save_seen_jobs(seen_urls: Dict[str, str], seen_titles: Dict[str, str]):
    """Save seen job URLs and titles to file (atomic operation to prevent race conditions)"""
    seen_jobs_file = 'seen_jobs.json'
    temp_file = 'seen_jobs.json.tmp'
    max_retries = 3

    # Expire entries older than 30 days
    cutoff = (datetime.now() - timedelta(days=30)).strftime("%Y-%m-%d")
    seen_urls = {u: d for u, d in seen_urls.items() if d >= cutoff}
    seen_titles = {t: d for t, d in seen_titles.items() if d >= cutoff}

    data = {
        'urls': seen_urls,
        'titles': seen_titles
    }
    
    for attempt in range(max_retries):
        try:
            # Write to temp file first, then rename (atomic on Unix)
            with open(temp_file, 'w') as f:
                json.dump(data, f, indent=2)
                f.flush()
                os.fsync(f.fileno())  # Force write to disk
            
            # Atomic rename (Unix) or copy (Windows)
            if os.name == 'nt':  # Windows
                if os.path.exists(seen_jobs_file):
                    os.remove(seen_jobs_file)
                os.rename(temp_file, seen_jobs_file)
            else:  # Unix/Linux/Mac
                os.rename(temp_file, seen_jobs_file)
            
            logger.info(f"Saved {len(seen_urls)} URLs and {len(seen_titles)} titles to memory")
            return
        except Exception as e:
            logger.warning(f"Error saving seen_jobs.json (attempt {attempt + 1}/{max_retries}): {e}")
            if attempt < max_retries - 1:
                time.sleep(0.2)  # Wait before retry
            else:
                logger.error(f"Failed to save seen_jobs.json after {max_retries} attempts")
        finally:
            # Clean up temp file if it exists
            try:
                if os.path.exists(temp_file):
                    os.remove(temp_file)
            except Exception:
                pass


def get_lock_file_path() -> str:
    """Get absolute path to lock file (based on script directory)"""
    script_dir = os.path.dirname(os.path.abspath(__file__))
    return os.path.join(script_dir, 'job_scraper.lock')


def kill_existing_instances():
    """Kill any existing instances of this script (except current process)"""
    current_pid = os.getpid()
    script_name = os.path.basename(__file__)
    
    try:
        import subprocess
        # Find all Python processes running main.py
        result = subprocess.run(
            ['pgrep', '-f', f'python.*{script_name}'],
            capture_output=True,
            text=True
        )
        
        if result.returncode == 0:
            pids = [pid.strip() for pid in result.stdout.strip().split('\n') if pid.strip()]
            killed_count = 0
            for pid in pids:
                try:
                    pid_int = int(pid)
                    if pid_int != current_pid:
                        os.kill(pid_int, 15)  # SIGTERM
                        killed_count += 1
                        logger.info(f"Killed existing instance (PID: {pid_int})")
                except (ValueError, ProcessLookupError, PermissionError):
                    pass  # Process already dead or permission denied
            
            if killed_count > 0:
                logger.info(f"Killed {killed_count} existing instance(s)")
                time.sleep(2)  # Give processes time to exit
    except Exception as e:
        logger.warning(f"Error killing existing instances: {e}")


def acquire_lock() -> bool:
    """Acquire a lock file to prevent multiple instances from running (atomic operation)"""
    lock_file = get_lock_file_path()
    max_retries = 5  # Increased retries
    retry_delay = 0.5  # 500ms between retries (increased)
    
    for attempt in range(max_retries):
        try:
            # Try to create lock file atomically (exclusive creation)
            # This prevents race conditions between checking and creating
            try:
                # Use O_CREAT | O_EXCL flags for atomic creation (Unix)
                # On Windows, this will raise FileExistsError if file exists
                fd = os.open(lock_file, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                with os.fdopen(fd, 'w') as f:
                    f.write(f"{os.getpid()}\n{time.time()}\n{BOT_VERSION}")
                logger.info("Lock acquired successfully")
                return True
            except (OSError, FileExistsError):
                # Lock file already exists
                if os.path.exists(lock_file):
                    # Check if lock is stale (older than 10 minutes)
                    try:
                        lock_age = time.time() - os.path.getmtime(lock_file)
                        if lock_age > 600:  # 10 minutes (reduced from 15)
                            logger.warning(f"Removing stale lock file (age: {lock_age:.0f}s)")
                            try:
                                os.remove(lock_file)
                            except OSError:
                                pass  # Another process might have removed it
                            # Wait a bit before retrying
                            if attempt < max_retries - 1:
                                time.sleep(retry_delay)
                                continue
                        else:
                            # Lock is active, check if it's the same process
                            try:
                                with open(lock_file, 'r') as f:
                                    lock_pid = f.readline().strip()
                                    if lock_pid == str(os.getpid()):
                                        logger.warning("Lock file exists but belongs to this process. Reusing.")
                                        return True
                            except Exception:
                                pass
                            
                            if attempt < max_retries - 1:
                                time.sleep(retry_delay)
                                continue
                            else:
                                logger.warning(f"Another instance is already running (lock age: {lock_age:.0f}s). Exiting.")
                                return False
                    except Exception as e:
                        logger.warning(f"Error checking lock file: {e}. Retrying...")
                        if attempt < max_retries - 1:
                            time.sleep(retry_delay)
                            continue
                        return False
                else:
                    # File doesn't exist but we got FileExistsError - race condition
                    if attempt < max_retries - 1:
                        time.sleep(retry_delay)
                        continue
                    return False
        except Exception as e:
            logger.error(f"Error acquiring lock (attempt {attempt + 1}/{max_retries}): {e}")
            if attempt < max_retries - 1:
                time.sleep(retry_delay)
                continue
    
    return False


def release_lock():
    """Release the lock file"""
    lock_file = get_lock_file_path()
    try:
        if os.path.exists(lock_file):
            os.remove(lock_file)
    except Exception as e:
        logger.warning(f"Error releasing lock: {e}")


async def run_daily_scrape_async(is_startup_run: bool = False):
    """Main async function to run daily scrape
    
    Args:
        is_startup_run: True if this is the first run on startup (show all weak matches immediately),
                       False if this is a scheduled run (split weak matches to 9:05 AM)
    """
    # Check for lock file to prevent multiple instances
    # Add a longer random delay to prevent race conditions when schedule triggers multiple times
    # This gives time for any previous instance to finish and release the lock
    delay = random.uniform(1.0, 3.0)  # Random delay 1-3 seconds
    logger.info(f"Waiting {delay:.2f}s before acquiring lock...")
    await asyncio.sleep(delay)
    
    if not acquire_lock():
        logger.warning("Another instance is running. Exiting to prevent duplicates.")
        return
    
    try:
        logger.info("=" * 60)
        logger.info("Starting daily job scrape...")
        logger.info("=" * 60)
        
        # Load seen jobs (both URLs and titles)
        seen_urls, seen_titles = load_seen_jobs()
        
        # Scrape all jobs
        jobs = await scrape_all_jobs()
        
        logger.info(f"Total jobs found: {len(jobs)}")
        
        # Filter out blacklisted jobs (already done in parsers, but double-check)
        filtered_jobs = [j for j in jobs if j.priority != JobPriority.BLACKLISTED]
        
        # Deduplicate: filter out jobs we've already seen (by URL or title)
        new_jobs = []
        skipped_count = 0
        skipped_urls = 0
        skipped_titles = 0
        
        for job in filtered_jobs:
            normalized_url = normalize_url(job.url)
            normalized_title = normalize_title(job.title)
            
            # Check both URL and title for duplicates
            if normalized_url in seen_urls:
                skipped_urls += 1
                skipped_count += 1
                continue
            
            if normalized_title in seen_titles:
                skipped_titles += 1
                skipped_count += 1
                continue
            
            # New job - add to both dicts with today's date
            new_jobs.append(job)
            today = datetime.now().strftime("%Y-%m-%d")
            seen_urls[normalized_url] = today
            seen_titles[normalized_title] = today
        
        if skipped_count > 0:
            logger.info(f"Skipped {skipped_count} duplicate jobs ({skipped_urls} by URL, {skipped_titles} by title)")
        
        logger.info(f"New jobs to send: {len(new_jobs)}")
        
        # Save seen jobs IMMEDIATELY after deduplication (before sending to Discord)
        # This prevents race condition where multiple instances send same jobs
        # Save even if no new jobs (to update the file timestamp)
        save_seen_jobs(seen_urls, seen_titles)
        
        # Only send to Discord if there are new jobs
        if new_jobs:
            # Send to Discord if webhook is configured
            # SECURITY: Webhook URL must be set via environment variable or .env file
            webhook_url = os.getenv('DISCORD_WEBHOOK_URL')
            
            # Debug: Log webhook status (without exposing the full URL)
            if webhook_url:
                webhook_preview = webhook_url[:50] + "..." if len(webhook_url) > 50 else webhook_url
                logger.info(f"Discord webhook configured: {webhook_preview}")
            else:
                logger.warning("DISCORD_WEBHOOK_URL not set. Check .env file or environment variables.")
                logger.warning("Current working directory: " + os.getcwd())
                logger.warning(".env file exists: " + str(os.path.exists('.env')))
            
            if webhook_url:
                try:
                    logger.info(f"Attempting to send {len(new_jobs)} jobs to Discord...")
                    notifier = DiscordNotifier(webhook_url)
                    # On startup, include all weak matches in the main message
                    # On scheduled runs, split weak matches to 9:05 AM
                    notifier.send_summary(new_jobs, include_all_weak_matches=is_startup_run)
                    logger.info("Successfully sent jobs to Discord!")
                except Exception as e:
                    logger.error(f"Error sending to Discord: {e}", exc_info=True)
                    # Fallback to console output
                    logger.info("Falling back to console output...")
                    for job in sorted(new_jobs, key=lambda x: x.priority.value):
                        print(f"\n[{job.priority.name}] {job.title} @ {job.company}")
                        print(f"  Reason: {job.priority_reason}")
                        print(f"  URL: {job.url}")
                        print(f"  Source: {job.source}")
            else:
                logger.warning("DISCORD_WEBHOOK_URL not set. Skipping Discord notification.")
                # Print summary to console
                for job in sorted(new_jobs, key=lambda x: x.priority.value):
                    print(f"\n[{job.priority.name}] {job.title} @ {job.company}")
                    print(f"  Reason: {job.priority_reason}")
                    print(f"  URL: {job.url}")
                    print(f"  Source: {job.source}")
        else:
            logger.info("No new jobs to send to Discord")
        
        logger.info("Daily scrape completed!")
        logger.info("=" * 60)
    finally:
        # Close browser after scraping
        await PlaywrightBrowserManager.close_browser()
        # Release lock file
        release_lock()


def run_daily_scrape(is_startup_run: bool = False):
    """Wrapper to run async scrape
    
    Args:
        is_startup_run: True if this is the first run on startup
    """
    asyncio.run(run_daily_scrape_async(is_startup_run=is_startup_run))


def send_startup_notification():
    """Send startup confirmation to Discord"""
    webhook_url = os.getenv('DISCORD_WEBHOOK_URL')
    if not webhook_url:
        logger.warning("DISCORD_WEBHOOK_URL not set. Cannot send startup notification.")
        return
    
    try:
        # Get hostname/server info
        import socket
        hostname = socket.gethostname()
        
        payload = {
            "content": f"🚀 **Bot Startup Notification**\n\n"
                      f"✅ Bot Version: **{BOT_VERSION}**\n"
                      f"🖥️ Server: **{hostname}**\n"
                      f"⏰ Started at: **{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}**\n"
                      f"📅 Next run: **09:00 AM daily**\n"
                      f"📊 Weak matches follow-up: **09:05 AM daily**\n\n"
                      f"_Monitoring: 🪙 Crypto/Web3 + 🚢 Cruise/Maritime IT + 💼 General/Remote_",
            "embeds": []
        }
        response = requests.post(webhook_url, json=payload, timeout=30)
        response.raise_for_status()
        logger.info(f"Sent startup notification to Discord (HTTP {response.status_code})")
    except Exception as e:
        logger.error(f"Error sending startup notification to Discord: {e}")


_shutdown_requested = False


def _handle_signal(signum, frame):
    """Handle SIGTERM/SIGINT for graceful shutdown."""
    global _shutdown_requested
    sig_name = signal.Signals(signum).name
    logger.info(f"Received {sig_name} — shutting down gracefully...")
    _shutdown_requested = True


def main():
    """Main entry point"""
    parser = argparse.ArgumentParser(description=f"Job Scraper Bot {BOT_VERSION}")
    parser.add_argument("--once", action="store_true", help="Run scrapers once then exit (no scheduler)")
    args = parser.parse_args()

    # Register signal handlers for graceful shutdown
    signal.signal(signal.SIGINT, _handle_signal)
    signal.signal(signal.SIGTERM, _handle_signal)

    # Kill any existing instances first (prevents duplicates from manual starts)
    kill_existing_instances()

    # Read schedule time from env (default 09:00)
    schedule_time = os.getenv("SCRAPE_SCHEDULE_TIME", "09:00")
    # Weak-matches follow-up 5 minutes later
    hh, mm = schedule_time.split(":")
    followup_time = f"{hh}:{int(mm) + 5:02d}"

    logger.info(f"Job Scraper Bot {BOT_VERSION} starting...")

    # Send startup notification to Discord
    send_startup_notification()

    try:
        if args.once:
            # Single run mode — scrape and exit
            logger.info("Running in --once mode (single scrape then exit)")
            run_daily_scrape(is_startup_run=True)
            return

        # Run immediately on start only if it's before the scheduled time
        now = datetime.now()
        sched_hour, sched_min = int(hh), int(mm)
        sched_today = now.replace(hour=sched_hour, minute=sched_min, second=0, microsecond=0)
        if now < sched_today:
            run_daily_scrape(is_startup_run=True)
        else:
            logger.info(f"Startup after {schedule_time} — skipping initial run. Next scrape at {schedule_time} tomorrow.")

        # Schedule daily runs
        schedule.every().day.at(schedule_time).do(run_daily_scrape, is_startup_run=False)
        schedule.every().day.at(followup_time).do(send_remaining_weak_matches)

        logger.info(f"Scheduled: daily scrape at {schedule_time}, weak-matches follow-up at {followup_time}")
        logger.info("Press Ctrl+C to stop")

        # Main loop with graceful shutdown support
        # Use short sleep intervals so Ctrl+C is responsive on Windows
        while not _shutdown_requested:
            schedule.run_pending()
            for _ in range(60):
                if _shutdown_requested:
                    break
                time.sleep(1)

        logger.info("Shutdown complete.")
        asyncio.run(PlaywrightBrowserManager.close_browser())

    except Exception as e:
        logger.error(f"Fatal error in main: {e}")
        raise
    finally:
        release_lock()


if __name__ == "__main__":
    main()
