<p align="center">
  <img src="https://img.shields.io/badge/python-3.10+-3776AB?style=for-the-badge&logo=python&logoColor=white" />
  <img src="https://img.shields.io/badge/Playwright-2bd27e?style=for-the-badge&logo=playwright&logoColor=white" />
  <img src="https://img.shields.io/badge/Discord-Webhooks-5865F2?style=for-the-badge&logo=discord&logoColor=white" />
  <img src="https://img.shields.io/badge/license-MIT-yellow?style=for-the-badge" />
</p>

<h1 align="center">Job Scraper Bot</h1>

<p align="center">
  <b>Automated daily job scraper that hunts Web3 / Crypto & Cruise / Maritime IT positions, ranks them by relevance, and delivers a prioritised report straight to Discord.</b>
</p>

<p align="center">
  <a href="#-quick-start">Quick Start</a> &bull;
  <a href="#-how-it-works">How It Works</a> &bull;
  <a href="#-scraped-sources">Sources</a> &bull;
  <a href="#%EF%B8%8F-configuration">Configuration</a> &bull;
  <a href="#-deployment">Deployment</a>
</p>

---

## Highlights

| Feature | Details |
|---|---|
| **15+ job sources** | Web3.career, CryptoJobsList, CryptocurrencyJobs, CryptoJobs, FindCryptoJobs, Bondex, RemoteOK, Wellfound, Delphi Ventures, Telegram channels, Carnival Ship Jobs, AllCruiseJobs, Selection Partners, PeopleConquest, Douro Azul |
| **Smart ranking** | Three-tier priority system ("Nuno Filter") — Perfect, Good, and Weak matches — plus automatic blacklisting of irrelevant roles |
| **Concurrent scraping** | All sources are scraped in parallel via `asyncio.gather` for maximum speed |
| **Deduplication** | URL + title normalisation prevents the same job from appearing twice across runs |
| **Discord reports** | Colour-coded embeds split into **Crypto / Web3** and **Cruise / Maritime IT** sections |
| **Web dashboard** | React UI + FastAPI: calendar, LLM job agents, resume → ranker overrides, **system status**, **hard refresh**, **Discord on/off** (without stopping scrapes) |
| **Stealth browsing** | Playwright with rotating User-Agents and anti-bot evasion |
| **Retry logic** | Automatic retries with exponential back-off on transient page-load failures |
| **Graceful shutdown** | Handles `SIGTERM` / `SIGINT` cleanly — safe for systemd and Docker |

---

## How It Works

```
┌──────────────┐     ┌──────────────┐     ┌──────────────┐     ┌──────────────┐
│   Scrape     │────▶│    Rank      │────▶│  Deduplicate │────▶│   Notify     │
│  15+ sources │     │  (Nuno       │     │  (URL +      │     │  (Discord    │
│  concurrently│     │   Filter)    │     │   title)     │     │   webhook)   │
└──────────────┘     └──────────────┘     └──────────────┘     └──────────────┘
```

1. **Scrape** — Playwright renders JS-heavy pages; BeautifulSoup parses the HTML. All scrapers run concurrently.
2. **Rank** — Each job is classified against your profile:
   - **Perfect Match** — DevOps, SysAdmin, SRE, Infrastructure, Node Operator + Linux/Python/Docker keywords
   - **Good Match** — IT Support, Datacenter Tech, Solutions Architect + hardware/network keywords
   - **Weak Match** — Generic technical roles with some relevance
   - **Blacklisted** — Marketing, Sales, HR, Legal, Finance — filtered out automatically
3. **Deduplicate** — Seen jobs are tracked in `seen_jobs.json` so you never get the same listing twice.
4. **Notify** — A colour-coded Discord report lands in your channel every morning.

---

## Scraped Sources

### Crypto / Web3

| Source | URL |
|---|---|
| Web3.career | `web3.career` |
| CryptoJobsList | `cryptojobslist.com` |
| CryptocurrencyJobs | `cryptocurrencyjobs.co` |
| CryptoJobs | `cryptojobs.com` |
| FindCryptoJobs | `findcryptojobs.xyz` |
| Bondex Network | `network.bondex.app` |
| RemoteOK | `remoteok.com` |
| Wellfound | `wellfound.com` |
| Delphi Ventures | `jobs.delphiventures.io` |
| Telegram | Multiple channels |

### Cruise / Maritime IT

| Source | URL |
|---|---|
| Carnival Ship Jobs | `shipjobs.carnival.com` |
| AllCruiseJobs | `allcruisejobs.com` |
| Selection Partners | `selectionpartners.net` |
| PeopleConquest | `peopleconquest.com` |
| Douro Azul | `douroazul.com` |

---

## Quick Start

### Prerequisites

- **Python 3.10+**
- A **Discord webhook URL** (optional but recommended)

### Install

```bash
git clone https://github.com/MonoBL/JobScraper.git
cd JobScraper

python -m venv venv
source venv/bin/activate        # Windows: venv\Scripts\activate

pip install -r requirements.txt
playwright install chromium      # downloads the headless browser
```

### Configure

```bash
cp .env.example .env
# edit .env and paste your Discord webhook URL
```

### Run

```bash
# Single scrape (great for testing)
python main.py --once

# Daemon mode — scrapes daily at 09:00 (default)
python main.py
```

---

## Configuration

All configuration is done via **environment variables** (or a `.env` file).

| Variable | Default | Description |
|---|---|---|
| `DISCORD_WEBHOOK_URL` | *(none)* | Discord webhook for notifications |
| `SCRAPE_SCHEDULE_TIME` | `09:00` | Daily scrape time (HH:MM, 24h format) |

### Web dashboard (`api/` + `web/`)

Run the API from the repo root, then the Vite dev server (or build the UI and serve static files from the API):

```bash
pip install -r requirements.txt
uvicorn api.app:app --reload --host 127.0.0.1 --port 8765
cd web && npm install && npm run dev    # http://localhost:5173 — proxies /api to 8765
```

- **System status** — pills for API, LLM, jobs, schedule, profile, and login; **Refresh status** re-checks endpoints; **Hard refresh** reloads the whole page.
- **Discord messages** — toggle stored in `data/app_settings.json` on the server. When **off**, `main.py` still scrapes and writes history but **does not** post to Discord (main report, 9:05 follow-up, or startup ping). You still need `DISCORD_WEBHOOK_URL` set when you want notifications again.

### Customising the Ranking

Edit the `JobRanker` class in `main.py` to adjust:

- `PERFECT_TITLES` / `PERFECT_KEYWORDS` — your ideal roles
- `GOOD_TITLES` / `GOOD_KEYWORDS` — secondary matches
- `BLACKLIST_TITLES` / `BLACKLIST_KEYWORDS` — roles to filter out

---

## Deployment

### systemd (Ubuntu / Debian)

See [systemd_setup.md](systemd_setup.md) for a step-by-step guide to run the bot 24/7 on a Linux server.

### Docker (coming soon)

A `Dockerfile` will be provided in a future release.

---

## Project Structure

```
JobScraper/
├── main.py                  # Scrapers, ranker, notifier, scheduler
├── app_settings.py          # Dashboard-persisted Discord on/off (under data/)
├── api/                     # FastAPI dashboard backend
├── web/                     # Vite + React dashboard frontend
├── test_cruise_scrapers.py  # Quick test for cruise/maritime scrapers
├── requirements.txt         # Python dependencies
├── .env.example             # Example environment file
├── .gitignore               # Git ignore rules
├── systemd_setup.md         # Linux deployment guide
└── README.md                # You are here
```

### Runtime files (git-ignored)

| File | Purpose |
|---|---|
| `seen_jobs.json` | Deduplication memory (URLs + titles) |
| `data/app_settings.json` | Dashboard toggle for Discord notifications (created when you use the UI) |
| `data/ranker_overrides.json` | LLM-added JobRanker phrases from resume review |
| `job_scraper.log` | Application log |
| `job_scraper.lock` | Prevents duplicate instances |
| `debug_*.png` | Screenshots saved when a scraper finds 0 jobs |

---

## CLI Reference

```
usage: main.py [-h] [--once]

Job Scraper Bot

options:
  -h, --help  show this help message and exit
  --once      Run scrapers once then exit (no scheduler)
```

---

## Troubleshooting

| Problem | Solution |
|---|---|
| **No jobs found** | Check `job_scraper.log` — the site's HTML may have changed. Update selectors in the relevant scraper class. |
| **Discord not receiving messages** | Verify `DISCORD_WEBHOOK_URL` is set. In the dashboard, ensure **Discord messages** is on and **System status → API** is green. Test the webhook with: `curl -X POST "$DISCORD_WEBHOOK_URL" -H "Content-Type: application/json" -d '{"content":"test"}'` |
| **Playwright errors** | Run `playwright install chromium` to ensure the browser is installed. |
| **Duplicate instance** | Delete `job_scraper.lock` if a previous run crashed without releasing it. |

---

## Contributing

Issues and pull requests are welcome! If a scraper stops working because a site changed its HTML, please open an issue with the site name and the error from the log.

---

## License

This project is open source and available for personal use.

---

<p align="center"><i>Built to bridge the gap from SysAdmin to Web3.</i></p>
