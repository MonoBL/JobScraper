# Fix and Run All Scrapers (Including Web3)

This guide explains how to recover broken scrapers, validate each source, and keep the bot running reliably.

## 1) Environment Setup

From the project root:

```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
python -m playwright install
```

Why this matters:
- `playwright` is required by dynamic sources (some Web3 boards need browser rendering).
- Installing browser binaries (`python -m playwright install`) is mandatory for Playwright-based scraping.

## 2) Configure Discord (Optional but Recommended)

Set webhook in your shell:

```bash
export DISCORD_WEBHOOK_URL="https://discord.com/api/webhooks/..."
```

If webhook is missing, scraping still runs, but results go to console/logs instead of Discord.

## 3) Run Once (Debug Mode)

Use a single run first:

```bash
python3 main.py
```

Then inspect logs:

```bash
rg "ERROR|WARNING|Failed to scrape|Scraped" job_scraper.log
```

What success looks like:
- You see lines like `Scraped X jobs from <Source>`.
- At the end, you see `Daily scrape completed!`.

## 4) Web3 Sources Covered

Current Web3/Crypto sources in `main.py`:
- `Web3CareerScraper`
- `CryptoJobsListScraper`
- `CryptocurrencyJobsScraper`
- `CryptoJobsScraper`
- `FindCryptoJobsScraper`
- `BondexScraper`
- `RemoteOKScraper`
- `WellfoundScraper`
- `DelphiVenturesScraper`
- `TelegramScraper`

Note:
- `SolanaJobsScraper` is intentionally disabled in code due to anti-bot behavior (403/download trigger).

## 5) How to Fix a Broken Scraper Fast

When one source fails, use this flow:

1. Identify failing source in `job_scraper.log` (`Failed to scrape <source>`).
2. Open that scraper class in `main.py`.
3. Validate:
   - URL still correct
   - CSS selectors still match page HTML
   - Response status and anti-bot behavior
4. If static parsing fails, switch that source to Playwright rendering.
5. Re-run `python3 main.py` and confirm recovered output.

## 6) Common Fix Patterns

### A) HTML changed (most common)
- Update `BeautifulSoup` selectors (`select`, `find`, class names, data attributes).
- Prefer stable selectors (IDs, `data-*` attributes) over fragile utility-class chains.

### B) JavaScript-rendered listings
- Use Playwright for page render before extracting elements.
- Add short waits for job cards to appear (explicit selector wait).

### C) Anti-bot / 403 issues
- Add realistic headers (`User-Agent`, `Accept-Language`).
- Reduce request rate and retry with backoff.
- Keep source disabled if it aggressively blocks automation (as done for Solana Jobs).

### D) Duplicate jobs or repeated spam
- Keep `seen_jobs.json` healthy.
- The project already deduplicates by normalized URL and normalized title.

## 7) Daily Operation (Production)

`main.py` already schedules:
- 09:00 scrape run
- 09:05 weak-match follow-up

Recommended:
- Run with your service manager (see `systemd_setup.md`).
- Monitor `job_scraper.log` daily for silent scraper regressions.

## 8) Quick Validation Checklist (All Scrapers)

- Environment installs cleanly (`pip install`, `playwright install`)
- `python3 main.py` completes without fatal errors
- Each major source logs a scrape result count
- Web3 sources produce non-zero jobs (except temporarily blocked sources)
- Discord receives message (if webhook configured)
- No repeated duplicate flood in consecutive runs

## 9) If You Want a Safer "Auto-Healing" Setup

Recommended future hardening:
- Add per-source health report (success/fail + count) at end of each run.
- Alert when a source returns 0 jobs for N consecutive days.
- Move each scraper into separate file for easier maintenance and testing.
- Add unit tests for parser selectors with saved HTML fixtures.

---

If you want, I can also add a second file with a per-source troubleshooting table (URL, selector target, likely failure mode, fix strategy) so debugging each Web3 board becomes faster.
