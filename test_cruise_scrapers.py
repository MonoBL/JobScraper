#!/usr/bin/env python3
"""Run only cruise/maritime IT scrapers and print results. Use: python test_cruise_scrapers.py"""
import asyncio
import sys
import os

# Ensure main can be imported
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from main import (
    CarnivalShipJobsScraper,
    AllCruiseJobsScraper,
    SelectionPartnersScraper,
    PeopleConquestScraper,
    DouroAzulScraper,
    PlaywrightBrowserManager,
)


async def main():
    scrapers = [
        CarnivalShipJobsScraper(),
        AllCruiseJobsScraper(),
        SelectionPartnersScraper(),
        PeopleConquestScraper(),
        DouroAzulScraper(),
    ]
    total = 0
    try:
        for scraper in scrapers:
            print(f"\n--- {scraper.source_name} ---")
            try:
                jobs = await scraper.scrape()
                for j in jobs[:5]:
                    print(f"  [{j.priority.name}] {j.title} @ {j.company}")
                    print(f"    {j.url[:70]}...")
                if len(jobs) > 5:
                    print(f"  ... and {len(jobs) - 5} more")
                print(f"  Total: {len(jobs)} jobs")
                total += len(jobs)
            except Exception as e:
                print(f"  ERROR: {e}")
    finally:
        await PlaywrightBrowserManager.close_browser()
    print(f"\n=== Total cruise IT jobs: {total} ===")
    return total


if __name__ == "__main__":
    asyncio.run(main())
