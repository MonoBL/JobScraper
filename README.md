# Daily Job Scraper & Ranking Bot

A custom Python bot that scrapes Web3/Crypto job boards daily and ranks them based on a hybrid Hardware/Software profile, specifically designed to help transition from SysAdmin/IT Support roles into the Crypto/Web3 space.

## 🎯 Features

- **Multi-Source Scraping**: Automatically scrapes jobs from:
  - [Web3.career](https://web3.career) (focus on technical support/infra roles)
  - [CryptoJobsList.com](https://cryptojobslist.com)
  - [CryptocurrencyJobs.co](https://cryptocurrencyjobs.co)

- **Smart Ranking System ("Nuno Filter")**:
  - 🥇 **Perfect Match**: Jobs matching "Junior DevOps", "SysAdmin", "L2 Support", "Infrastructure Engineer", "Node Operator" with Linux/Ubuntu + Python/Bash requirements
  - 🥈 **Good Match**: IT Support, Technical Support, Datacenter Technician roles with Hardware/Network keywords
  - 🥉 **Weak Match**: Generic customer support roles
  - 🚫 **Blacklisted**: Automatically filters out Marketing, Sales, HR, Legal, and Senior Solidity Developer roles

- **Discord Notifications**: Sends beautifully formatted job summaries to Discord via webhook
- **Error Handling**: Robust error handling ensures one scraper failure doesn't stop the others
- **Scheduled Runs**: Automatically runs daily at 09:00 AM (configurable)

## 🚀 Quick Start

### Prerequisites

- Python 3.10 or higher
- A Discord webhook URL (optional but recommended)

### Installation

1. **Clone the repository**:
   ```bash
   git clone https://github.com/MonoBL/JobScraper.git
   cd JobScraper
   ```

2. **Create a virtual environment** (recommended):
   ```bash
   python3 -m venv venv
   source venv/bin/activate  # On Windows: venv\Scripts\activate
   ```

3. **Install dependencies**:
   ```bash
   pip install -r requirements.txt
   ```

4. **Configure Discord webhook** (optional):
   ```bash
   # Option 1: Create .env file
   cp .env.example .env
   # Edit .env and add your Discord webhook URL
   
   # Option 2: Set environment variable
   export DISCORD_WEBHOOK_URL="https://discord.com/api/webhooks/YOUR_WEBHOOK_URL"
   ```

### Usage

**Run once** (for testing):
```bash
python3 main.py
```

**Run as a service** (24/7 on Ubuntu):
See [systemd_setup.md](systemd_setup.md) for detailed instructions on setting up as a systemd service.

## 📋 Configuration

### Discord Webhook Setup

1. Go to your Discord server
2. Navigate to: **Server Settings** → **Integrations** → **Webhooks**
3. Click **New Webhook**
4. Copy the webhook URL
5. Add it to your `.env` file or set as environment variable

### Customizing the Schedule

Edit `main.py` and change the schedule time:
```python
# Change this line (around line 572)
schedule.every().day.at("09:00").do(run_daily_scrape)
```

### Adjusting Ranking Criteria

The ranking logic is in the `JobRanker` class in `main.py`. You can customize:
- Perfect match titles and keywords
- Good match titles and keywords
- Blacklist terms

## 📁 Project Structure

```
JobScraper/
├── main.py                 # Main scraper script
├── requirements.txt        # Python dependencies
├── systemd_setup.md        # Ubuntu systemd deployment guide
├── .env                    # Environment variables (not in git)
├── .env.example           # Example environment file
├── .gitignore             # Git ignore rules
├── README.md              # This file
└── job_scraper.log        # Application logs (generated at runtime)
```

## 🔧 How It Works

1. **Scraping**: Each scraper (Web3.career, CryptoJobsList, CryptocurrencyJobs) fetches job listings
2. **Ranking**: Each job is analyzed against the ranking criteria
3. **Filtering**: Blacklisted jobs are removed
4. **Notification**: Jobs are grouped by priority and sent to Discord
5. **Logging**: All activity is logged to `job_scraper.log`

## 📊 Output Format

Jobs are sent to Discord in priority order:
- **Perfect Matches** (🥇): Green embed with top priority jobs
- **Good Matches** (🥈): Blue embed with secondary priority jobs
- **Weak Matches** (🥉): Red embed (only shown if no perfect/good matches)

Each job includes:
- Job title and company
- Priority reason
- Direct link to the job posting
- Source (which job board)

## 🛠️ Troubleshooting

### No jobs found
- Check the application log: `tail -f job_scraper.log`
- Job board HTML structure may have changed - you may need to update scraper selectors

### Discord webhook not working
- Verify the webhook URL is correct
- Test manually:
  ```bash
  curl -X POST "YOUR_WEBHOOK_URL" \
    -H "Content-Type: application/json" \
    -d '{"content": "Test message"}'
  ```

### Scraper errors
- Each scraper has independent error handling
- Check logs for specific error messages
- Network issues may cause temporary failures

## 📝 Logging

The bot logs to:
- **Console**: Real-time output
- **job_scraper.log**: Persistent log file
- **System logs**: When running as systemd service (see systemd_setup.md)

## 🔒 Security Notes

- Never commit your `.env` file or Discord webhook URL to git
- The `.gitignore` file is configured to exclude sensitive files
- Use environment variables or `.env` files for configuration

## 🤝 Contributing

Feel free to submit issues or pull requests if you find bugs or want to add features!

## 📄 License

This project is open source and available for personal use.

## 🙏 Acknowledgments

Built to help transition from SysAdmin/IT Support roles into the Web3/Crypto space.

---

**Note**: Job board HTML structures may change over time. If scrapers stop working, you may need to update the selectors in the respective scraper classes.

