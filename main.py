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
        "junior devops", "sysadmin", "system administrator",
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
        "customer support engineer", "technical support engineer"
    ]
    GOOD_KEYWORDS = [
        "hardware", "repair", "network", "networking", "tickets",
        "on-site", "onsite", "equipment", "server maintenance",
        "troubleshooting", "monitoring", "alerting", "incident response"
    ]
    
    # Blacklist - only filter out clearly irrelevant roles (less strict)
    BLACKLIST_TITLES = [
        "senior solidity developer", "marketing manager", "sales manager",
        "hr manager", "human resources manager", "legal counsel", "lawyer", "attorney",
        "cfo", "cto", "founder", "co-founder", "product manager", "product owner",
        "ui/ux designer", "content writer", "copywriter", "community manager",
        "social media manager", "influencer", "accountant", "finance manager"
    ]
    BLACKLIST_KEYWORDS = [
        "senior solidity", "marketing manager", "sales manager",
        "hr manager", "legal counsel", "10+ years experience", "15+ years",
        "phd required", "masters required", "bachelor's degree required"
    ]
    
    # Additional filter: only filter out clearly irrelevant patterns (less strict)
    IRRELEVANT_PATTERNS = [
        r'\b(chief|founder|co-founder|cto|ceo|cfo)\s+\w+',
        r'\b(15|20)\+?\s*years?\s+experience',
        r'\b(phd|masters?)\s+required',
        r'\b(marketing|sales|legal|finance|accounting)\s+manager',
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

        # Check Perfect Match (need title + at least 1 keyword)
        has_perfect_title = JobRanker.contains_keywords(title_lower, JobRanker.PERFECT_TITLES)
        has_linux = JobRanker.contains_keywords(combined, JobRanker.PERFECT_KEYWORDS['linux'])
        has_scripting = JobRanker.contains_keywords(combined, JobRanker.PERFECT_KEYWORDS['scripting'])
        has_infra = JobRanker.contains_keywords(combined, JobRanker.PERFECT_KEYWORDS['infrastructure'])

        keyword_count = sum([has_linux, has_scripting, has_infra])
        
        if has_perfect_title and keyword_count >= 1:
            return JobPriority.PERFECT_MATCH, f"Perfect match: Title + {keyword_count} technical keyword(s)"

        # Check Good Match (more lenient: title OR keywords)
        has_good_title = JobRanker.contains_keywords(title_lower, JobRanker.GOOD_TITLES)
        has_good_keywords = JobRanker.contains_keywords(combined, JobRanker.GOOD_KEYWORDS)

        if has_good_title and has_good_keywords:
            return JobPriority.GOOD_MATCH, "Good match: IT Support title + technical keywords"
        
        if has_good_title or has_good_keywords:
            return JobPriority.GOOD_MATCH, "Good match: IT Support/Hardware/Network keywords"
        
        if keyword_count >= 1:
            return JobPriority.GOOD_MATCH, f"Good match: Technical keywords found ({keyword_count})"

        # Weak Match: More lenient - include jobs with any technical or support relevance
        if any(kw in combined for kw in ['support', 'operations', 'infrastructure', 'linux', 'python', 'devops', 
                                         'technical', 'engineer', 'developer', 'admin', 'sysadmin', 'it', 
                                         'network', 'server', 'cloud', 'kubernetes', 'docker', 'monitoring']):
            return JobPriority.WEAK_MATCH, "Weak match: Some technical relevance"

        # Default: Weak Match for any crypto/web3 job (don't blacklist by default)
        if any(kw in combined for kw in ['crypto', 'blockchain', 'web3', 'defi', 'bitcoin', 'ethereum', 'nft']):
            return JobPriority.WEAK_MATCH, "Weak match: Crypto/Web3 related"

        # Only blacklist if truly irrelevant
        return JobPriority.WEAK_MATCH, "Weak match: Generic role - review manually"


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
    """Base scraper class using Playwright"""
    
    def __init__(self, source_name: str, base_url: str, job_list_selector: str = None, wait_timeout: int = 30000):
        self.source_name = source_name
        self.base_url = base_url
        self.search_url = base_url
        self.job_list_selector = job_list_selector  # Selector to wait for job list to load
        self.wait_timeout = wait_timeout  # Timeout in milliseconds

    async def scrape(self) -> List[Job]:
        """Scrape jobs from the source. Override in subclasses."""
        raise NotImplementedError

    def parse_job(self, element, job_url: str = None) -> Optional[Job]:
        """Parse a job element. Override in subclasses."""
        raise NotImplementedError
    
    async def get_page_content(self, url: str, take_screenshot: bool = False, screenshot_path: str = None) -> tuple[Optional[str], Optional[Page]]:
        """Get page content after JavaScript rendering
        
        Returns:
            tuple: (content, page) - Returns content and page object if take_screenshot is True
        """
        page = None
        try:
            page = await PlaywrightBrowserManager.create_page()
            logger.info(f"Loading {url}...")
            
            # Use 'domcontentloaded' instead of 'networkidle' to avoid timeouts
            await page.goto(url, wait_until='domcontentloaded', timeout=self.wait_timeout)
            
            # Wait for body to ensure page is ready
            try:
                await page.wait_for_selector('body', timeout=5000)
            except PlaywrightTimeoutError:
                logger.warning(f"Body not found for {self.source_name}, continuing anyway...")
            
            # Wait for job list to load if selector is provided
            if self.job_list_selector:
                try:
                    # Try to wait for any job-related element
                    await page.wait_for_selector('body', timeout=5000)
                    # Additional wait for content to render
                    await page.wait_for_timeout(3000)  # 3 seconds for JS to render
                    logger.info(f"Page loaded for {self.source_name}")
                except PlaywrightTimeoutError:
                    logger.warning(f"Timeout waiting for content on {self.source_name}, continuing anyway...")
            
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
        """Scrape Web3.career"""
        jobs = []
        page = None
        try:
            logger.info(f"Scraping {self.source_name}...")
            content, _ = await self.get_page_content(self.search_url)
            
            if not content:
                # Try homepage as fallback
                logger.info(f"Trying homepage for {self.source_name}...")
                content, _ = await self.get_page_content(self.base_url)
            
            if not content:
                return jobs
            
            soup = BeautifulSoup(content, 'html.parser')
            
            # Web3.career: Uses table structure - target tbody tr or div.table_row
            job_elements = soup.select('tbody tr')
            
            if not job_elements:
                # Try div.table_row
                job_elements = soup.find_all('div', class_=re.compile(r'table_row|table-row', re.I))
            
            if not job_elements:
                # Fallback: Try divs with row class
                job_elements = soup.find_all('div', class_=re.compile(r'row', re.I))
            
            if not job_elements:
                # Try table rows without tbody
                job_elements = soup.find_all('tr', class_=re.compile(r'job|listing|row', re.I))
            
            if not job_elements:
                # Try article or div elements with job-related classes
                job_elements = soup.find_all(['article', 'div'], class_=re.compile(r'job|listing|card', re.I))
            
            if not job_elements:
                # Try links to job pages
                job_elements = soup.find_all('a', href=re.compile(r'/job/|/jobs/'))
            
            if not job_elements:
                # Try more generic selectors
                job_elements = soup.find_all(['div', 'li'], attrs={'data-job-id': True}) or \
                              soup.find_all(['div', 'li'], class_=re.compile(r'item|post', re.I))
            
            logger.info(f"Found {len(job_elements)} potential job elements from {self.source_name}")
            
            # Debug: Take screenshot if 0 jobs found
            if len(job_elements) == 0:
                logger.warning("Found 0 jobs. Taking debug screenshot...")
                # Re-fetch page with screenshot
                screenshot_path = "debug_web3_career.png"
                content, page = await self.get_page_content(self.search_url, take_screenshot=True, screenshot_path=screenshot_path)
                if not content:
                    # Try homepage
                    content, page = await self.get_page_content(self.base_url, take_screenshot=True, screenshot_path=screenshot_path)
                logger.info(f"Debug screenshot saved to {screenshot_path}")
            
            # Debug: Print first element HTML if elements found but no jobs parsed
            if len(job_elements) > 0:
                first_element_html = str(job_elements[0])[:500]  # First 500 chars
                logger.debug(f"First element HTML sample: {first_element_html}")
            
            for element in job_elements[:20]:  # Limit to first 20
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
    """Scraper for CryptoJobsList.com"""
    
    def __init__(self):
        super().__init__(
            "CryptoJobsList.com",
            "https://cryptojobslist.com",
            job_list_selector='article, [class*="job"], [class*="listing"]',
            wait_timeout=30000
        )
        self.search_url = "https://cryptojobslist.com"

    async def scrape(self) -> List[Job]:
        """Scrape CryptoJobsList.com"""
        jobs = []
        try:
            logger.info(f"Scraping {self.source_name}...")
            content, _ = await self.get_page_content(self.search_url)
            
            if not content:
                return jobs
            
            soup = BeautifulSoup(content, 'html.parser')
            
            # Look for job listings
            job_elements = soup.find_all(['article', 'div', 'li'], class_=re.compile(r'job|listing|card|item', re.I))
            
            if not job_elements:
                job_elements = soup.find_all('a', href=re.compile(r'/job/|/jobs/|/position/'))
            
            if not job_elements:
                # Try more generic selectors
                job_elements = soup.find_all(['div', 'section'], attrs={'data-job': True}) or \
                              soup.find_all(['div', 'li'], class_=re.compile(r'post|entry', re.I))
            
            logger.info(f"Found {len(job_elements)} potential job elements from {self.source_name}")
            
            # Debug: Print first element HTML if elements found
            if len(job_elements) > 0:
                first_element_html = str(job_elements[0])[:500]  # First 500 chars
                logger.debug(f"First element HTML sample: {first_element_html}")
            
            for element in job_elements[:20]:
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
        
        return jobs

    def parse_job(self, element, job_url: str = None) -> Optional[Job]:
        """Parse a CryptoJobsList.com job element"""
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
        """Scrape CryptocurrencyJobs.co - using H2 headings approach"""
        jobs = []
        try:
            logger.info(f"Scraping {self.source_name}...")
            content, _ = await self.get_page_content(self.search_url)
            
            if not content:
                return jobs
            
            soup = BeautifulSoup(content, 'html.parser')
            
            # Search for H2 headings in main (job titles are H2)
            main_elem = soup.find('main')
            if not main_elem:
                return jobs
            
            h2_elements = main_elem.find_all('h2')
            
            logger.info(f"Found {len(h2_elements)} H2 headings from {self.source_name}")
            
            for h2_elem in h2_elements[:30]:  # Limit to first 30
                try:
                    # Get text content
                    title_text = h2_elem.get_text(strip=True)
                    
                    # Filter: Skip if "Talent Collective" or "Subscribe"
                    if not title_text or len(title_text) < 5:
                        continue
                    if 'talent collective' in title_text.lower() or 'subscribe' in title_text.lower():
                        continue
                    
                    # Find parent <a> tag or closest ancestor
                    link = h2_elem.find_parent('a')
                    if not link:
                        # Try finding a link near the H2 (sibling or parent's sibling)
                        parent = h2_elem.parent
                        if parent:
                            link = parent.find('a')
                    
                    # Get URL
                    url = self.base_url
                    if link and link.get('href'):
                        href = link['href']
                        if not href.startswith('http'):
                            url = f"{self.base_url}{href}"
                        else:
                            url = href
                    
                    # Create job from H2
                    job = self.parse_job_from_h2(title_text, url)
                    if job:
                        jobs.append(job)
                except Exception as e:
                    logger.warning(f"Error parsing H2 element: {e}")
                    continue
                    
        except Exception as e:
            logger.error(f"Error scraping {self.source_name}: {e}")
        
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
        """Scrape Delphi Ventures job board"""
        jobs = []
        try:
            logger.info(f"Scraping {self.source_name}...")
            content, _ = await self.get_page_content(self.search_url)
            
            if not content:
                return jobs
            
            soup = BeautifulSoup(content, 'html.parser')
            
            # Getro job boards typically use article or div with job classes
            # Look for job cards - they usually have company name, title, location
            job_elements = soup.find_all(['article', 'div'], class_=re.compile(r'job|listing|card|item', re.I))
            
            # Alternative: look for links to job pages
            if not job_elements:
                job_elements = soup.find_all('a', href=re.compile(r'/jobs/|/job/'))
            
            logger.info(f"Found {len(job_elements)} potential job elements from {self.source_name}")
            
            for element in job_elements[:30]:  # Limit to first 30
                try:
                    job = self.parse_job(element)
                    if job:
                        jobs.append(job)
                except Exception as e:
                    logger.warning(f"Error parsing job element: {e}")
                    continue
                    
        except Exception as e:
            logger.error(f"Error scraping {self.source_name}: {e}")
        
        return jobs

    def parse_job(self, element, job_url: str = None) -> Optional[Job]:
        """Parse a Delphi Ventures job element"""
        try:
            # Getro structure: usually has company name, job title, location
            # Title is often in h2, h3, or h4
            title_elem = element.find(['h2', 'h3', 'h4', 'h5'])
            if not title_elem:
                # Try finding title in a link
                title_elem = element.find('a', href=re.compile(r'/jobs/|/job/'))
            
            if not title_elem:
                return None
            
            title = title_elem.get_text(strip=True)
            if not title or len(title) < 5:
                return None
            
            # Skip if it's clearly not a job (e.g., "Subscribe", "Get in touch")
            if any(skip in title.lower() for skip in ['subscribe', 'get in touch', 'privacy', 'cookie']):
                return None
            
            # Extract URL
            link = element.find('a', href=True)
            if link and link.get('href'):
                url = link['href']
                if not url.startswith('http'):
                    url = f"{self.base_url}{url}"
            else:
                # Try to find link in parent
                parent_link = element.find_parent('a', href=True)
                if parent_link:
                    url = parent_link['href']
                    if not url.startswith('http'):
                        url = f"{self.base_url}{url}"
                else:
                    url = job_url or self.base_url
            
            # Extract company name (often appears before title or in a separate element)
            company = "Unknown"
            # Look for company in common patterns
            company_elem = element.find(['span', 'div', 'p'], class_=re.compile(r'company|employer|organization', re.I))
            if company_elem:
                company = company_elem.get_text(strip=True)
            else:
                # Try to find company name in text (often appears as "CompanyName\nJob Title")
                text_parts = element.get_text('\n', strip=True).split('\n')
                if len(text_parts) > 1:
                    # First non-empty line might be company
                    for part in text_parts[:3]:
                        if part and len(part) < 50 and part.lower() != title.lower():
                            company = part.strip()
                            break
            
            # Extract description
            desc_elem = element.find(['p', 'div'], class_=re.compile(r'description|summary|excerpt', re.I))
            description = desc_elem.get_text(strip=True) if desc_elem else ""
            
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
            embed = {
                "title": "🥇 Perfect Matches",
                "description": f"**{len(perfect_matches)}** perfect match(es) found!",
                "color": 3066993,  # Green
                "fields": []
            }
            for job in perfect_matches[:8]:  # Limit to 8 for readability
                embed["fields"].append({
                    "name": f"**{job.title}**",
                    "value": f"🏢 {job.company}\n📝 {job.priority_reason}\n🔗 [View Job]({job.url})\n📍 *{job.source}*",
                    "inline": False
                })
            embeds.append(embed)
        
        # Embed 2: Good Matches
        if good_matches:
            embed = {
                "title": "🥈 Good Matches",
                "description": f"**{len(good_matches)}** good match(es) found",
                "color": 15844367,  # Gold
                "fields": []
            }
            for job in good_matches[:8]:  # Limit to 8
                embed["fields"].append({
                    "name": f"**{job.title}**",
                    "value": f"🏢 {job.company}\n📝 {job.priority_reason}\n🔗 [View Job]({job.url})\n📍 *{job.source}*",
                    "inline": False
                })
            embeds.append(embed)
        
        # Embed 3: Telegram Finds (grouped by channel)
        if telegram_jobs:
            telegram_by_channel = {}
            for job in telegram_jobs:
                channel = job.source
                if channel not in telegram_by_channel:
                    telegram_by_channel[channel] = []
                telegram_by_channel[channel].append(job)
            
            embed = {
                "title": "📱 Telegram Finds",
                "description": f"**{len(telegram_jobs)}** job(s) from Telegram channels",
                "color": 3447003,  # Blue
                "fields": []
            }
            for job in telegram_jobs[:8]:  # Limit to 8
                embed["fields"].append({
                    "name": f"**{job.title}**",
                    "value": f"🏢 {job.company}\n🔗 [View Message]({job.url})\n📍 *{job.source}*",
                    "inline": False
                })
            embeds.append(embed)
        
        # Embed 4: Weak Matches (condensed)
        if weak_matches:
            # Group weak matches by source
            weak_by_source = {}
            for job in weak_matches:
                source = job.source
                if source not in weak_by_source:
                    weak_by_source[source] = []
                weak_by_source[source].append(job)
            
            embed = {
                "title": "🔍 Other Potential Roles",
                "description": f"**{len(weak_matches)}** weak match(es) - review manually",
                "color": 9807270,  # Grey
                "fields": []
            }
            
            # Show up to 6 weak matches (condensed format)
            for job in weak_matches[:6]:
                embed["fields"].append({
                    "name": f"{job.title}",
                    "value": f"🏢 {job.company} | 🔗 [View]({job.url}) | 📍 {job.source}",
                    "inline": True
                })
            embeds.append(embed)
        
        # Main message header
        content_text = f"🤖 **Bot Version: {BOT_VERSION}**\n"
        content_text += f"📊 **Daily Job Scraper Report** - {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\n"
        content_text += f"**Summary:**\n"
        content_text += f"🥇 Perfect: {len(perfect_matches)} | "
        content_text += f"🥈 Good: {len(good_matches)} | "
        content_text += f"🔍 Weak: {len(weak_matches)}"
        if telegram_jobs:
            content_text += f" | 📱 Telegram: {len(telegram_jobs)}"
        
        # If there are more weak matches, mention them
        if len(weak_matches) > 6:
            content_text += f"\n\n*Showing top 6 weak matches. {len(weak_matches) - 6} more available in logs.*"
        
        payload = {
            "content": content_text,
            "embeds": embeds
        }
        
        try:
            response = requests.post(self.webhook_url, json=payload, timeout=10)
            response.raise_for_status()
            logger.info(f"Successfully sent {len(jobs)} jobs to Discord")
        except Exception as e:
            logger.error(f"Error sending to Discord: {e}")
            raise


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
            
            if webhook_url:
                try:
                    notifier = DiscordNotifier(webhook_url)
                    notifier.send_summary(new_jobs)
                except Exception as e:
                    logger.error(f"Error sending to Discord: {e}")
                    # Fallback to console output
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
        
        logger.info("Job scraper started. Will run daily at 09:00 AM")
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
