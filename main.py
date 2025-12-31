#!/usr/bin/env python3
"""
Daily Job Scraper & Ranking Bot
Scrapes Web3/Crypto job boards and ranks them based on Nuno's profile.
"""

import os
import re
import json
import logging
from datetime import datetime
from typing import List, Dict, Optional
from dataclasses import dataclass, asdict
from enum import Enum

import requests
from bs4 import BeautifulSoup
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


class JobScraper:
    """Base scraper class"""
    
    def __init__(self, source_name: str, base_url: str):
        self.source_name = source_name
        self.base_url = base_url
        self.session = requests.Session()
        self.session.headers.update({
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
        })

    def scrape(self) -> List[Job]:
        """Scrape jobs from the source. Override in subclasses."""
        raise NotImplementedError

    def parse_job(self, element, job_url: str = None) -> Optional[Job]:
        """Parse a job element. Override in subclasses."""
        raise NotImplementedError


class Web3CareerScraper(JobScraper):
    """Scraper for Web3.career"""
    
    def __init__(self):
        super().__init__("Web3.career", "https://web3.career")
        # Focus on technical support/infra roles
        self.search_url = "https://web3.career/jobs?keywords=devops+sysadmin+support+infrastructure"

    def scrape(self) -> List[Job]:
        """Scrape Web3.career"""
        jobs = []
        try:
            logger.info(f"Scraping {self.source_name}...")
            response = self.session.get(self.search_url, timeout=30)
            response.raise_for_status()
            
            soup = BeautifulSoup(response.content, 'html.parser')
            
            # Web3.career structure may vary - this is a generic approach
            # Look for job listings (common selectors)
            job_elements = soup.find_all(['article', 'div'], class_=re.compile(r'job|listing|card', re.I))
            
            if not job_elements:
                # Try alternative selectors
                job_elements = soup.find_all('a', href=re.compile(r'/job/|/jobs/'))
            
            for element in job_elements[:20]:  # Limit to first 20
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
        """Parse a Web3.career job element"""
        try:
            # Extract title
            title_elem = element.find(['h2', 'h3', 'a'], class_=re.compile(r'title|job-title', re.I))
            if not title_elem:
                title_elem = element.find('a', href=re.compile(r'/job/'))
            
            if not title_elem:
                return None
            
            title = title_elem.get_text(strip=True)
            
            # Extract URL
            link = element.find('a', href=True)
            if link:
                url = link['href']
                if not url.startswith('http'):
                    url = f"{self.base_url}{url}"
            else:
                url = job_url or self.base_url
            
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
        super().__init__("CryptoJobsList.com", "https://cryptojobslist.com")
        self.search_url = "https://cryptojobslist.com/jobs"

    def scrape(self) -> List[Job]:
        """Scrape CryptoJobsList.com"""
        jobs = []
        try:
            logger.info(f"Scraping {self.source_name}...")
            response = self.session.get(self.search_url, timeout=30)
            response.raise_for_status()
            
            soup = BeautifulSoup(response.content, 'html.parser')
            
            # Look for job listings
            job_elements = soup.find_all(['article', 'div', 'li'], class_=re.compile(r'job|listing|card|item', re.I))
            
            if not job_elements:
                job_elements = soup.find_all('a', href=re.compile(r'/job/|/jobs/|/position/'))
            
            for element in job_elements[:20]:
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
        """Parse a CryptoJobsList.com job element"""
        try:
            title_elem = element.find(['h2', 'h3', 'h4', 'a'], class_=re.compile(r'title|name|job', re.I))
            if not title_elem:
                title_elem = element.find('a', href=re.compile(r'/job/'))
            
            if not title_elem:
                return None
            
            title = title_elem.get_text(strip=True)
            
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
        super().__init__("CryptocurrencyJobs.co", "https://cryptocurrencyjobs.co")
        self.search_url = "https://cryptocurrencyjobs.co"

    def scrape(self) -> List[Job]:
        """Scrape CryptocurrencyJobs.co"""
        jobs = []
        try:
            logger.info(f"Scraping {self.source_name}...")
            response = self.session.get(self.search_url, timeout=30)
            response.raise_for_status()
            
            soup = BeautifulSoup(response.content, 'html.parser')
            
            job_elements = soup.find_all(['article', 'div', 'li'], class_=re.compile(r'job|listing|card|post', re.I))
            
            if not job_elements:
                job_elements = soup.find_all('a', href=re.compile(r'/job/|/jobs/|/position/'))
            
            for element in job_elements[:20]:
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
        """Parse a CryptocurrencyJobs.co job element"""
        try:
            title_elem = element.find(['h2', 'h3', 'h4', 'a'], class_=re.compile(r'title|name|job', re.I))
            if not title_elem:
                title_elem = element.find('a', href=re.compile(r'/job/'))
            
            if not title_elem:
                return None
            
            title = title_elem.get_text(strip=True)
            
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
        
        # Group by priority
        perfect_matches = [j for j in sorted_jobs if j.priority == JobPriority.PERFECT_MATCH]
        good_matches = [j for j in sorted_jobs if j.priority == JobPriority.GOOD_MATCH]
        weak_matches = [j for j in sorted_jobs if j.priority == JobPriority.WEAK_MATCH]
        
        # Build embed
        embeds = []
        
        # Perfect Matches
        if perfect_matches:
            embed = {
                "title": "🥇 Perfect Matches",
                "color": 3066993,  # Green
                "fields": []
            }
            for job in perfect_matches[:10]:  # Limit to 10
                embed["fields"].append({
                    "name": f"{job.title} @ {job.company}",
                    "value": f"{job.priority_reason}\n[View Job]({job.url})\n*{job.source}*",
                    "inline": False
                })
            embeds.append(embed)
        
        # Good Matches
        if good_matches:
            embed = {
                "title": "🥈 Good Matches",
                "color": 3447003,  # Blue
                "fields": []
            }
            for job in good_matches[:10]:
                embed["fields"].append({
                    "name": f"{job.title} @ {job.company}",
                    "value": f"{job.priority_reason}\n[View Job]({job.url})\n*{job.source}*",
                    "inline": False
                })
            embeds.append(embed)
        
        # Weak Matches (only if there are no perfect/good matches)
        if weak_matches and not perfect_matches and not good_matches:
            embed = {
                "title": "🥉 Weak Matches",
                "color": 15158332,  # Red
                "fields": []
            }
            for job in weak_matches[:5]:
                embed["fields"].append({
                    "name": f"{job.title} @ {job.company}",
                    "value": f"{job.priority_reason}\n[View Job]({job.url})\n*{job.source}*",
                    "inline": False
                })
            embeds.append(embed)
        
        # Main message
        payload = {
            "content": f"📊 **Daily Job Scraper Report** - {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n"
                      f"Found {len(perfect_matches)} perfect matches, {len(good_matches)} good matches, {len(weak_matches)} weak matches",
            "embeds": embeds
        }
        
        try:
            response = requests.post(self.webhook_url, json=payload, timeout=10)
            response.raise_for_status()
            logger.info(f"Successfully sent {len(jobs)} jobs to Discord")
        except Exception as e:
            logger.error(f"Error sending to Discord: {e}")


def scrape_all_jobs() -> List[Job]:
    """Scrape all job sources"""
    all_jobs = []
    scrapers = [
        Web3CareerScraper(),
        CryptoJobsListScraper(),
        CryptocurrencyJobsScraper()
    ]
    
    for scraper in scrapers:
        try:
            jobs = scraper.scrape()
            all_jobs.extend(jobs)
            logger.info(f"Scraped {len(jobs)} jobs from {scraper.source_name}")
        except Exception as e:
            logger.error(f"Failed to scrape {scraper.source_name}: {e}")
            continue
    
    return all_jobs


def run_daily_scrape():
    """Main function to run daily scrape"""
    logger.info("=" * 60)
    logger.info("Starting daily job scrape...")
    logger.info("=" * 60)
    
    # Scrape all jobs
    jobs = scrape_all_jobs()
    
    logger.info(f"Total jobs found: {len(jobs)}")
    
    # Filter out blacklisted jobs (already done in parsers, but double-check)
    filtered_jobs = [j for j in jobs if j.priority != JobPriority.BLACKLISTED]
    
    # Send to Discord if webhook is configured
    # Default webhook URL (can be overridden by environment variable)
    default_webhook = "REPLACED_WEBHOOK_URL"
    webhook_url = os.getenv('DISCORD_WEBHOOK_URL', default_webhook)
    
    if webhook_url:
        try:
            notifier = DiscordNotifier(webhook_url)
            notifier.send_summary(filtered_jobs)
        except Exception as e:
            logger.error(f"Error sending to Discord: {e}")
            # Fallback to console output
            for job in sorted(filtered_jobs, key=lambda x: x.priority.value):
                print(f"\n[{job.priority.name}] {job.title} @ {job.company}")
                print(f"  Reason: {job.priority_reason}")
                print(f"  URL: {job.url}")
                print(f"  Source: {job.source}")
    else:
        logger.warning("DISCORD_WEBHOOK_URL not set. Skipping Discord notification.")
        # Print summary to console
        for job in sorted(filtered_jobs, key=lambda x: x.priority.value):
            print(f"\n[{job.priority.name}] {job.title} @ {job.company}")
            print(f"  Reason: {job.priority_reason}")
            print(f"  URL: {job.url}")
            print(f"  Source: {job.source}")
    
    logger.info("Daily scrape completed!")
    logger.info("=" * 60)


def main():
    """Main entry point"""
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


if __name__ == "__main__":
    main()

