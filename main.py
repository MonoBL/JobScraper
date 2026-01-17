#!/usr/bin/env python3
"""
Daily Job Scraper & Ranking Bot
Scrapes Web3/Crypto job boards and ranks them based on Nuno's profile.
Uses Playwright for JavaScript-rendered content.
"""

# Bot version - update this when making significant changes
BOT_VERSION = "v2.1"

import os
import re
import json
import logging
import asyncio
import random
from datetime import datetime
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

    def to_dict(self):
        return {
            **asdict(self),
            'priority': self.priority.name,
            'priority_value': self.priority.value
        }


class JobRanker:
    """Ranks jobs based on Nuno's profile"""
    
    # Perfect Match criteria
    PERFECT_TITLES = [
        "junior devops", "sysadmin", "system administrator", "systems administrator",
        "it systems administrator", "it system administrator", "it administrator",
        "l2 support", "level 2 support", "infrastructure engineer",
        "node operator", "node operations", "site reliability engineer",
        "sre", "devops engineer", "platform engineer"
    ]
    PERFECT_KEYWORDS = {
        'linux': ['linux', 'ubuntu', 'debian', 'centos'],
        'scripting': ['python', 'bash', 'shell scripting', 'shell script'],
        'infrastructure': ['kubernetes', 'docker', 'terraform', 'ansible', 'ci/cd']
    }
    
    # Good Match criteria
    GOOD_TITLES = [
        "it support", "technical support", "datacenter technician",
        "it operations", "operations engineer", "support engineer",
        "customer support engineer", "technical support engineer",
        "solutions architect", "systems architect", "technical architect",
        "infrastructure architect", "cloud architect"
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
        "cfo", "cto", "founder", "co-founder", "product manager", "product owner",
        "ui/ux designer", "content writer", "copywriter", "community manager",
        "social media manager", "influencer", "accountant", "finance manager",
        "affiliate manager", "business development", "bd manager", "legal admin",
        "senior associate", "analyst", "client insights", "sales analytics",
        "product control", "business operations", "strategy manager", "operations manager",
        "internal audit", "professional practices", "compliance", "regulatory"
    ]
    BLACKLIST_KEYWORDS = [
        "senior solidity", "marketing manager", "sales manager",
        "hr manager", "legal counsel", "10+ years experience", "15+ years",
        "phd required", "masters required", "bachelor's degree required",
        "affiliate", "business development", "bd", "legal", "compliance",
        "audit", "accounting", "finance", "analyst", "sales analytics",
        "client insights", "product control", "strategy", "operations manager"
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
        has_perfect_title = JobRanker.contains_keywords(title_lower, JobRanker.PERFECT_TITLES)
        has_linux = JobRanker.contains_keywords(combined, JobRanker.PERFECT_KEYWORDS['linux'])
        has_scripting = JobRanker.contains_keywords(combined, JobRanker.PERFECT_KEYWORDS['scripting'])
        has_infra = JobRanker.contains_keywords(combined, JobRanker.PERFECT_KEYWORDS['infrastructure'])

        keyword_count = sum([has_linux, has_scripting, has_infra])
        
        # Perfect match: Strong IT title (like "IT Systems Administrator") is enough
        # OR title + technical keywords
        if has_perfect_title:
            if keyword_count >= 1:
                return JobPriority.PERFECT_MATCH, f"Perfect match: Title + {keyword_count} technical keyword(s)"
            # Strong IT titles like "IT Systems Administrator" are perfect even without keywords
            if any(term in title_lower for term in ['system administrator', 'systems administrator', 'sysadmin', 'it administrator']):
                return JobPriority.PERFECT_MATCH, "Perfect match: Strong IT/Systems Administrator title"

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
                             'site reliability', 'infrastructure engineer', 'technical support']
        
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
        """Create a new page with stealth settings"""
        browser = await cls.get_browser()
        page = await browser.new_page()
        
        # Set realistic user agent
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
    
    async def click_load_more(self, page: Page, button_selector: str, max_clicks: int = 5) -> int:
        """Click 'Load More' button if it exists. Returns number of clicks."""
        clicks = 0
        try:
            for _ in range(max_clicks):
                # Try multiple common selectors for "Load More" buttons
                selectors = [
                    button_selector,
                    'button:has-text("Load More")',
                    'button:has-text("Show More")',
                    'a:has-text("Load More")',
                    '[class*="load-more"]',
                    '[class*="show-more"]',
                    '[id*="load-more"]',
                ]
                
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
                    except:
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


class DiscordNotifier:
    """Send job summaries to Discord via webhook"""
    
    def __init__(self, webhook_url: str):
        self.webhook_url = webhook_url
    
    def send_summary(self, jobs: List[Job]):
        """Send formatted job summary to Discord with improved layout"""
        if not jobs:
            logger.info("No jobs to send to Discord")
            return
        
        # Sort jobs by priority
        sorted_jobs = sorted(jobs, key=lambda x: x.priority.value)
        
        # Separate by source and priority
        telegram_jobs = [j for j in sorted_jobs if 'Telegram' in j.source]
        other_jobs = [j for j in sorted_jobs if 'Telegram' not in j.source]
        
        # Group other jobs by priority
        perfect_matches = [j for j in other_jobs if j.priority == JobPriority.PERFECT_MATCH]
        good_matches = [j for j in other_jobs if j.priority == JobPriority.GOOD_MATCH]
        weak_matches = [j for j in other_jobs if j.priority == JobPriority.WEAK_MATCH]
        
        # Group by source for better organization
        jobs_by_source = {}
        for job in other_jobs:
            source = job.source
            if source not in jobs_by_source:
                jobs_by_source[source] = []
            jobs_by_source[source].append(job)
        
        # Build embeds with improved layout
        embeds = []
        
        # Embed 1: Perfect Matches (separate for visibility)
        if perfect_matches:
            # Split perfect matches into chunks if too many
            perfect_chunks = [perfect_matches[i:i + 8] for i in range(0, len(perfect_matches), 8)]
            for chunk_idx, chunk in enumerate(perfect_chunks):
                embed = {
                    "title": "🥇 Perfect Matches" + (f" (Part {chunk_idx + 1})" if len(perfect_chunks) > 1 else ""),
                    "description": f"**{len(perfect_matches)}** perfect match(es) found!" + (f" - Part {chunk_idx + 1} of {len(perfect_chunks)}" if len(perfect_chunks) > 1 else ""),
                    "color": 3066993,  # Green
                    "fields": []
                }
                for job in chunk:
                    # More compact format
                    title = job.title[:80] + "..." if len(job.title) > 80 else job.title
                    embed["fields"].append({
                        "name": f"**{title}**",
                        "value": f"🏢 {job.company}\n🔗 [View Job]({job.url})\n📍 {job.source}",
                        "inline": False
                    })
                embeds.append(embed)
        
        # Embed 2: Good Matches
        if good_matches:
            # Split good matches into chunks if too many
            good_chunks = [good_matches[i:i + 8] for i in range(0, len(good_matches), 8)]
            for chunk_idx, chunk in enumerate(good_chunks):
                embed = {
                    "title": "🥈 Good Matches" + (f" (Part {chunk_idx + 1})" if len(good_chunks) > 1 else ""),
                    "description": f"**{len(good_matches)}** good match(es) found" + (f" - Part {chunk_idx + 1} of {len(good_chunks)}" if len(good_chunks) > 1 else ""),
                    "color": 15844367,  # Gold
                    "fields": []
                }
                for job in chunk:
                    # More compact format
                    title = job.title[:80] + "..." if len(job.title) > 80 else job.title
                    embed["fields"].append({
                        "name": f"**{title}**",
                        "value": f"🏢 {job.company}\n🔗 [View Job]({job.url})\n📍 {job.source}",
                        "inline": False
                    })
                embeds.append(embed)
        
        # Embed 3: Telegram Finds (grouped by channel)
        if telegram_jobs:
            # Split Telegram jobs into chunks if too many
            telegram_chunks = [telegram_jobs[i:i + 10] for i in range(0, len(telegram_jobs), 10)]
            for chunk_idx, chunk in enumerate(telegram_chunks):
                embed = {
                    "title": "📱 Telegram Finds" + (f" (Part {chunk_idx + 1})" if len(telegram_chunks) > 1 else ""),
                    "description": f"**{len(telegram_jobs)}** job(s) from Telegram" + (f" - Part {chunk_idx + 1} of {len(telegram_chunks)}" if len(telegram_chunks) > 1 else ""),
                    "color": 3447003,  # Blue
                    "fields": []
                }
                for job in chunk:
                    # More compact format, truncate long titles
                    title = job.title[:70] + "..." if len(job.title) > 70 else job.title
                    embed["fields"].append({
                        "name": title,
                        "value": f"[View]({job.url})",
                        "inline": False
                    })
                embeds.append(embed)
        
        # Weak matches: Use text format instead of embeds to avoid size limits
        # Show first batch in main message, save remaining for 9:30 AM message
        weak_matches_text = ""
        remaining_weak_matches = []
        if weak_matches:
            weak_matches_text = f"\n\n**🔍 Other Potential Roles ({len(weak_matches)} weak matches):**\n"
            # Show first batch of weak matches in main message
            # Limit to first 20 to stay under 2000 char limit for content
            shown_count = 0
            for job in weak_matches:
                title = job.title[:80] + "..." if len(job.title) > 80 else job.title
                company = job.company[:30] + "..." if len(job.company) > 30 else job.company
                line = f"• {title} @ {company} - [View]({job.url})\n"
                # Check if adding this line would exceed limit
                if len(weak_matches_text) + len(line) > 1800:
                    remaining_weak_matches = weak_matches[shown_count:]
                    remaining = len(remaining_weak_matches)
                    weak_matches_text += f"\n*... and {remaining} more weak matches (will be sent at 9:05 AM)*"
                    break
                weak_matches_text += line
                shown_count += 1
            
            # Save remaining weak matches to file for 9:30 AM message
            if remaining_weak_matches:
                save_remaining_weak_matches(remaining_weak_matches)
        
        # Discord has a limit of 10 embeds per message and 2000 chars for content
        # Split into multiple messages if needed
        max_embeds_per_message = 10
        embed_chunks = [embeds[i:i + max_embeds_per_message] for i in range(0, len(embeds), max_embeds_per_message)]
        
        for msg_idx, embed_chunk in enumerate(embed_chunks):
            # Main message header (only on first message)
            if msg_idx == 0:
                content_text = f"🤖 **Bot Version: {BOT_VERSION}**\n"
                content_text += f"📊 **Daily Job Scraper Report** - {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\n"
                content_text += f"**Summary:**\n"
                content_text += f"🥇 Perfect: {len(perfect_matches)} | "
                content_text += f"🥈 Good: {len(good_matches)} | "
                content_text += f"🔍 Weak: {len(weak_matches)}"
                if telegram_jobs:
                    content_text += f" | 📱 Telegram: {len(telegram_jobs)}"
                
                # Add weak matches as text (only in first message)
                content_text += weak_matches_text
                
                if len(embed_chunks) > 1:
                    content_text += f"\n\n*Message {msg_idx + 1} of {len(embed_chunks)}*"
            else:
                content_text = f"*Continued... (Message {msg_idx + 1} of {len(embed_chunks)})*"
            
            payload = {
                "content": content_text,
                "embeds": embed_chunk
            }
            
            try:
                logger.debug(f"Sending message {msg_idx + 1}/{len(embed_chunks)} to Discord (embeds: {len(embed_chunk)})")
                response = requests.post(self.webhook_url, json=payload, timeout=30)
                response.raise_for_status()
                logger.info(f"Successfully sent message {msg_idx + 1}/{len(embed_chunks)} to Discord (HTTP {response.status_code})")
                # Small delay between messages to avoid rate limiting
                if msg_idx < len(embed_chunks) - 1:
                    import time
                    time.sleep(1)
            except requests.exceptions.RequestException as e:
                logger.error(f"Error sending message {msg_idx + 1} to Discord: {e}")
                if hasattr(e, 'response') and e.response is not None:
                    logger.error(f"Discord API response: {e.response.status_code} - {e.response.text[:500]}")
                raise
            except Exception as e:
                logger.error(f"Unexpected error sending message {msg_idx + 1} to Discord: {e}", exc_info=True)
                raise
        
        logger.info(f"Successfully sent main message with {len(jobs)} jobs to Discord in {len(embed_chunks)} message(s)")
        if remaining_weak_matches:
            logger.info(f"Saved {len(remaining_weak_matches)} remaining weak matches for 9:30 AM message")


async def scrape_all_jobs() -> List[Job]:
    """Scrape all job sources"""
    all_jobs = []
    scrapers = [
        Web3CareerScraper(),
        CryptoJobsListScraper(),
        CryptocurrencyJobsScraper(),
        DelphiVenturesScraper(),
        TelegramScraper()
    ]
    
    for scraper in scrapers:
        try:
            jobs = await scraper.scrape()
            all_jobs.extend(jobs)
            logger.info(f"Scraped {len(jobs)} jobs from {scraper.source_name}")
        except Exception as e:
            logger.error(f"Failed to scrape {scraper.source_name}: {e}")
            continue
    
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


def load_seen_jobs() -> Tuple[Set[str], Set[str]]:
    """Load seen job URLs and titles from file
    
    Returns:
        tuple: (seen_urls_set, seen_titles_set)
    """
    seen_jobs_file = 'seen_jobs.json'
    seen_urls = set()
    seen_titles = set()
    
    try:
        if os.path.exists(seen_jobs_file):
            with open(seen_jobs_file, 'r') as f:
                data = json.load(f)
                
                # Handle both old format (list of URLs) and new format (dict)
                if isinstance(data, list):
                    # Old format: just URLs
                    seen_urls = {normalize_url(url) for url in data}
                elif isinstance(data, dict):
                    # New format: {urls: [...], titles: [...]}
                    seen_urls = {normalize_url(url) for url in data.get('urls', [])}
                    seen_titles = {normalize_title(title) for title in data.get('titles', [])}
                
                logger.info(f"Loaded {len(seen_urls)} seen URLs and {len(seen_titles)} seen titles from memory")
    except Exception as e:
        logger.warning(f"Error loading seen_jobs.json: {e}")
    
    return seen_urls, seen_titles


def save_remaining_weak_matches(remaining_jobs: List[Job]):
    """Save remaining weak matches to file for 9:30 AM message"""
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
                        posted_date=job_data.get('posted_date')
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


def save_seen_jobs(seen_urls: Set[str], seen_titles: Set[str]):
    """Save seen job URLs and titles to file (atomic operation to prevent race conditions)"""
    seen_jobs_file = 'seen_jobs.json'
    temp_file = 'seen_jobs.json.tmp'
    max_retries = 3
    
    # Limit size to prevent file from growing too large (keep last 5000)
    if len(seen_urls) > 5000:
        seen_urls = set(list(seen_urls)[-5000:])
    if len(seen_titles) > 5000:
        seen_titles = set(list(seen_titles)[-5000:])
    
    data = {
        'urls': list(seen_urls),
        'titles': list(seen_titles)
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
            except:
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
                            except:
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
                            except:
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


async def run_daily_scrape_async():
    """Main async function to run daily scrape"""
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
            
            # New job - add to both sets
            new_jobs.append(job)
            seen_urls.add(normalized_url)
            seen_titles.add(normalized_title)
        
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
                    notifier.send_summary(new_jobs)
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


def run_daily_scrape():
    """Wrapper to run async scrape"""
    asyncio.run(run_daily_scrape_async())


def main():
    """Main entry point"""
    # Kill any existing instances first (prevents duplicates from manual starts)
    kill_existing_instances()
    
    # Don't acquire lock here - let each scrape run acquire its own lock
    # This prevents the main process from holding the lock while scheduler runs
    logger.info(f"Job Scraper Bot {BOT_VERSION} starting...")
    
    try:
        # Run immediately on start (for testing)
        run_daily_scrape()
        
        # Schedule daily runs at 9:00 AM
        schedule.every().day.at("09:00").do(run_daily_scrape)
        # Schedule remaining weak matches at 9:05 AM
        schedule.every().day.at("09:05").do(send_remaining_weak_matches)
        
        logger.info("Job scraper started. Will run daily at 09:00 AM")
        logger.info("Remaining weak matches will be sent at 09:05 AM")
        logger.info("Press Ctrl+C to stop")
        
        # Keep the script running
        try:
            while True:
                schedule.run_pending()
                time.sleep(60)  # Check every minute
        except KeyboardInterrupt:
            logger.info("Job scraper stopped by user")
            # Close browser on exit
            asyncio.run(PlaywrightBrowserManager.close_browser())
    except Exception as e:
        logger.error(f"Fatal error in main: {e}")
        raise


if __name__ == "__main__":
    main()
