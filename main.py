#!/usr/bin/env python3
"""
Daily Job Scraper & Ranking Bot
Scrapes Web3/Crypto job boards and ranks them based on Nuno's profile.
Uses Playwright for JavaScript-rendered content.
"""

import os
import re
import json
import logging
import asyncio
from datetime import datetime
from typing import List, Dict, Optional
from dataclasses import dataclass, asdict
from enum import Enum

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
        "node operator", "node operations"
    ]
    PERFECT_KEYWORDS = {
        'linux': ['linux', 'ubuntu'],
        'scripting': ['python', 'bash', 'shell scripting']
    }
    
    # Good Match criteria
    GOOD_TITLES = [
        "it support", "technical support", "datacenter technician",
        "it operations", "operations engineer", "support engineer"
    ]
    GOOD_KEYWORDS = [
        "hardware", "repair", "network", "networking", "tickets",
        "on-site", "onsite", "equipment", "server maintenance"
    ]
    
    # Blacklist
    BLACKLIST_TITLES = [
        "senior solidity developer", "marketing", "sales", "hr",
        "human resources", "legal", "lawyer", "attorney"
    ]
    BLACKLIST_KEYWORDS = [
        "senior solidity", "marketing manager", "sales manager",
        "hr manager", "legal counsel"
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

        # Check blacklist first
        if JobRanker.contains_keywords(combined, JobRanker.BLACKLIST_TITLES):
            return JobPriority.BLACKLISTED, "Contains blacklisted title/keyword"
        
        if JobRanker.contains_keywords(combined, JobRanker.BLACKLIST_KEYWORDS):
            return JobPriority.BLACKLISTED, "Contains blacklisted keyword"

        # Check Perfect Match
        has_perfect_title = JobRanker.contains_keywords(title_lower, JobRanker.PERFECT_TITLES)
        has_linux = JobRanker.contains_keywords(combined, JobRanker.PERFECT_KEYWORDS['linux'])
        has_scripting = JobRanker.contains_keywords(combined, JobRanker.PERFECT_KEYWORDS['scripting'])

        if has_perfect_title and has_linux and has_scripting:
            return JobPriority.PERFECT_MATCH, "Perfect match: Title + Linux + Python/Bash"

        # Check Good Match
        has_good_title = JobRanker.contains_keywords(title_lower, JobRanker.GOOD_TITLES)
        has_good_keywords = JobRanker.contains_keywords(combined, JobRanker.GOOD_KEYWORDS)

        if has_good_title or has_good_keywords:
            return JobPriority.GOOD_MATCH, "Good match: IT Support/Hardware/Network keywords"

        # Weak Match (generic customer support without technical keywords)
        if "customer support" in combined or "support" in title_lower:
            return JobPriority.WEAK_MATCH, "Generic support role"

        # Default: Weak Match for any other job
        return JobPriority.WEAK_MATCH, "No strong match criteria"


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
                
                # Get last 10 messages
                for message_wrap in message_wraps[-10:]:
                    try:
                        job = self.parse_job(message_wrap, channel_name)
                        if job:
                            jobs.append(job)
                    except Exception as e:
                        logger.warning(f"Error parsing Telegram message: {e}")
                        continue
                
                all_jobs.extend(jobs)
                logger.info(f"Scraped {len(jobs)} jobs from {channel_name}")
                        
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
            
            # Look for job keywords
            text_lower = message_text.lower()
            if not any(keyword in text_lower for keyword in ['hiring', 'role:', 'salary', 'position', 'job', 'looking for']):
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
        """Send formatted job summary to Discord"""
        if not jobs:
            logger.info("No jobs to send to Discord")
            return
        
        # Sort jobs by priority
        sorted_jobs = sorted(jobs, key=lambda x: x.priority.value)
        
        # Separate Telegram jobs from other sources
        telegram_jobs = [j for j in sorted_jobs if 'Telegram' in j.source]
        other_jobs = [j for j in sorted_jobs if 'Telegram' not in j.source]
        
        # Group other jobs by priority
        perfect_matches = [j for j in other_jobs if j.priority == JobPriority.PERFECT_MATCH]
        good_matches = [j for j in other_jobs if j.priority == JobPriority.GOOD_MATCH]
        weak_matches = [j for j in other_jobs if j.priority == JobPriority.WEAK_MATCH]
        
        # Build embeds with cleaner layout
        embeds = []
        
        # Embed 1: Top Matches (Perfect & Good combined)
        top_matches = perfect_matches + good_matches
        if top_matches:
            embed = {
                "title": "🏆 Top Matches",
                "color": 15844367,  # Gold
                "fields": []
            }
            # Limit to 10 to avoid Discord message length limits
            for job in top_matches[:10]:
                priority_emoji = "🥇" if job.priority == JobPriority.PERFECT_MATCH else "🥈"
                embed["fields"].append({
                    "name": f"{priority_emoji} **{job.title}** @ {job.company}",
                    "value": f"{job.priority_reason}\n[View Job]({job.url})\n*{job.source}*",
                    "inline": False
                })
            embeds.append(embed)
        
        # Embed 2: Telegram Finds
        if telegram_jobs:
            embed = {
                "title": "📱 Telegram Finds",
                "color": 3447003,  # Blue
                "fields": []
            }
            # Limit to 10 to avoid Discord message length limits
            for job in telegram_jobs[:10]:
                embed["fields"].append({
                    "name": f"**{job.title}**",
                    "value": f"[View Message]({job.url})\n*{job.source}*",
                    "inline": False
                })
            embeds.append(embed)
        
        # Build weak matches as text list at the bottom (to save space)
        weak_matches_text = ""
        if weak_matches:
            weak_matches_text = "\n\n**🔍 Other Potential Roles (Weak Match):**\n"
            # Limit to 10 to avoid Discord message length limits
            for job in weak_matches[:10]:
                weak_matches_text += f"• {job.title} @ {job.company} - [View Job]({job.url})\n"
        
        # Main message
        content_text = f"📊 **Daily Job Scraper Report** - {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n"
        content_text += f"Found {len(perfect_matches)} perfect matches, {len(good_matches)} good matches, {len(weak_matches)} weak matches"
        if telegram_jobs:
            content_text += f", {len(telegram_jobs)} Telegram finds"
        content_text += weak_matches_text
        
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


async def scrape_all_jobs() -> List[Job]:
    """Scrape all job sources"""
    all_jobs = []
    scrapers = [
        Web3CareerScraper(),
        CryptoJobsListScraper(),
        CryptocurrencyJobsScraper(),
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


def load_seen_jobs() -> set:
    """Load seen job URLs from file"""
    seen_jobs_file = 'seen_jobs.json'
    try:
        if os.path.exists(seen_jobs_file):
            with open(seen_jobs_file, 'r') as f:
                seen_urls = json.load(f)
                logger.info(f"Loaded {len(seen_urls)} seen job URLs from memory")
                return set(seen_urls)
    except Exception as e:
        logger.warning(f"Error loading seen_jobs.json: {e}")
    return set()


def save_seen_jobs(seen_urls: set):
    """Save seen job URLs to file"""
    seen_jobs_file = 'seen_jobs.json'
    try:
        with open(seen_jobs_file, 'w') as f:
            json.dump(list(seen_urls), f, indent=2)
        logger.info(f"Saved {len(seen_urls)} job URLs to memory")
    except Exception as e:
        logger.error(f"Error saving seen_jobs.json: {e}")


def acquire_lock() -> bool:
    """Acquire a lock file to prevent multiple instances from running (atomic operation)"""
    lock_file = 'job_scraper.lock'
    try:
        # Try to create lock file atomically (exclusive creation)
        # This prevents race conditions between checking and creating
        try:
            # Use O_CREAT | O_EXCL flags for atomic creation (Unix)
            # On Windows, this will raise FileExistsError if file exists
            fd = os.open(lock_file, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            with os.fdopen(fd, 'w') as f:
                f.write(str(os.getpid()))
            logger.info("Lock acquired successfully")
            return True
        except (OSError, FileExistsError):
            # Lock file already exists
            if os.path.exists(lock_file):
                # Check if lock is stale (older than 15 minutes)
                try:
                    lock_age = time.time() - os.path.getmtime(lock_file)
                    if lock_age > 900:  # 15 minutes
                        logger.warning(f"Removing stale lock file (age: {lock_age:.0f}s)")
                        os.remove(lock_file)
                        # Try again after removing stale lock
                        try:
                            fd = os.open(lock_file, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                            with os.fdopen(fd, 'w') as f:
                                f.write(str(os.getpid()))
                            logger.info("Lock acquired after removing stale lock")
                            return True
                        except (OSError, FileExistsError):
                            logger.warning("Another instance acquired lock. Exiting.")
                            return False
                    else:
                        logger.warning(f"Another instance is already running (lock age: {lock_age:.0f}s). Exiting.")
                        return False
                except Exception as e:
                    logger.warning(f"Error checking lock file: {e}. Exiting to be safe.")
                    return False
            return False
    except Exception as e:
        logger.error(f"Error acquiring lock: {e}")
        return False


def release_lock():
    """Release the lock file"""
    lock_file = 'job_scraper.lock'
    try:
        if os.path.exists(lock_file):
            os.remove(lock_file)
    except Exception as e:
        logger.warning(f"Error releasing lock: {e}")


async def run_daily_scrape_async():
    """Main async function to run daily scrape"""
    # Check for lock file to prevent multiple instances
    if not acquire_lock():
        logger.warning("Another instance is running. Exiting to prevent duplicates.")
        return
    
    try:
        logger.info("=" * 60)
        logger.info("Starting daily job scrape...")
        logger.info("=" * 60)
        
        # Load seen jobs
        seen_urls = load_seen_jobs()
        
        # Scrape all jobs
        jobs = await scrape_all_jobs()
        
        logger.info(f"Total jobs found: {len(jobs)}")
        
        # Filter out blacklisted jobs (already done in parsers, but double-check)
        filtered_jobs = [j for j in jobs if j.priority != JobPriority.BLACKLISTED]
        
        # Deduplicate: filter out jobs we've already seen
        new_jobs = []
        skipped_count = 0
        for job in filtered_jobs:
            if job.url in seen_urls:
                skipped_count += 1
                continue
            new_jobs.append(job)
            seen_urls.add(job.url)
        
        if skipped_count > 0:
            logger.info(f"Skipped {skipped_count} duplicate jobs")
        
        logger.info(f"New jobs to send: {len(new_jobs)}")
        
        # Save seen jobs IMMEDIATELY after deduplication (before sending to Discord)
        # This prevents race condition where multiple instances send same jobs
        # Save even if no new jobs (to update the file timestamp)
        save_seen_jobs(seen_urls)
        
        # Only send to Discord if there are new jobs
        if new_jobs:
            # Send to Discord if webhook is configured
            # Default webhook URL (can be overridden by environment variable)
            default_webhook = "REPLACED_WEBHOOK_URL"
            webhook_url = os.getenv('DISCORD_WEBHOOK_URL', default_webhook)
            
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
    # Check for lock at main entry point (prevent multiple script instances)
    if not acquire_lock():
        logger.error("Another instance is already running. Exiting.")
        return
    
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
    finally:
        # Release lock on exit
        release_lock()


if __name__ == "__main__":
    main()
