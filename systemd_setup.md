# Systemd Setup Guide for Job Scraper Bot

This guide will help you set up the job scraper bot to run 24/7 on your Ubuntu server using systemd.

## Prerequisites

1. Python 3.10+ installed on your Ubuntu server
2. The job scraper files (`main.py` and `requirements.txt`) in a directory on your server
3. A Discord webhook URL (optional but recommended)

## Step 1: Install Dependencies

```bash
# Navigate to the project directory
cd /path/to/scraper_jobs

# Create a virtual environment (recommended)
python3 -m venv venv
source venv/bin/activate

# Install dependencies
pip install -r requirements.txt
```

## Step 2: Set Up Environment Variables

Create a `.env` file or export the Discord webhook URL:

```bash
# Option 1: Export in your shell (temporary)
export DISCORD_WEBHOOK_URL="https://discord.com/api/webhooks/YOUR_WEBHOOK_URL"

# Option 2: Create a .env file (recommended for systemd)
echo 'DISCORD_WEBHOOK_URL=https://discord.com/api/webhooks/YOUR_WEBHOOK_URL' > .env
```

**Note:** For systemd, we'll use an EnvironmentFile instead (see Step 4).

## Step 3: Test the Script

Before setting up systemd, test that the script works:

```bash
# Activate virtual environment if using one
source venv/bin/activate

# Run the script manually
python3 main.py
```

Press `Ctrl+C` after it runs once to stop it. Verify that:
- Jobs are being scraped (check the logs)
- Discord notifications are working (if webhook is configured)

## Step 4: Create Systemd Service File

Create a systemd service file:

```bash
sudo nano /etc/systemd/system/job-scraper.service
```

Add the following content (adjust paths as needed):

```ini
[Unit]
Description=Daily Job Scraper & Ranking Bot
After=network.target

[Service]
Type=simple
User=YOUR_USERNAME
WorkingDirectory=/home/YOUR_USERNAME/path/to/scraper_jobs
Environment="PATH=/home/YOUR_USERNAME/path/to/scraper_jobs/venv/bin:/usr/local/bin:/usr/bin:/bin"
Environment="DISCORD_WEBHOOK_URL=https://discord.com/api/webhooks/YOUR_WEBHOOK_URL"
ExecStart=/home/YOUR_USERNAME/path/to/scraper_jobs/venv/bin/python3 /home/YOUR_USERNAME/path/to/scraper_jobs/main.py
Restart=always
RestartSec=10
StandardOutput=append:/var/log/job-scraper/output.log
StandardError=append:/var/log/job-scraper/error.log

[Install]
WantedBy=multi-user.target
```

**Important:** Replace the following placeholders:
- `YOUR_USERNAME`: Your Ubuntu username
- `/home/YOUR_USERNAME/path/to/scraper_jobs`: Full path to your project directory
- `https://discord.com/api/webhooks/YOUR_WEBHOOK_URL`: Your actual Discord webhook URL

### Alternative: Using EnvironmentFile

If you prefer to store the webhook URL in a file:

1. Create an environment file:
```bash
sudo nano /etc/job-scraper/env.conf
```

2. Add:
```
DISCORD_WEBHOOK_URL=https://discord.com/api/webhooks/YOUR_WEBHOOK_URL
```

3. Update the service file to include:
```ini
EnvironmentFile=/etc/job-scraper/env.conf
```

4. Set proper permissions:
```bash
sudo chmod 600 /etc/job-scraper/env.conf
```

## Step 5: Create Log Directory

```bash
sudo mkdir -p /var/log/job-scraper
sudo chown YOUR_USERNAME:YOUR_USERNAME /var/log/job-scraper
```

## Step 6: Reload Systemd and Start Service

```bash
# Reload systemd to recognize the new service
sudo systemctl daemon-reload

# Enable the service to start on boot
sudo systemctl enable job-scraper.service

# Start the service
sudo systemctl start job-scraper.service

# Check the status
sudo systemctl status job-scraper.service
```

## Step 7: Verify It's Working

Check the logs:

```bash
# View service logs
sudo journalctl -u job-scraper.service -f

# Or check the log files
tail -f /var/log/job-scraper/output.log
tail -f /var/log/job-scraper/error.log

# Check the application log
tail -f /path/to/scraper_jobs/job_scraper.log
```

## Useful Commands

```bash
# Start the service
sudo systemctl start job-scraper.service

# Stop the service
sudo systemctl stop job-scraper.service

# Restart the service
sudo systemctl restart job-scraper.service

# Check status
sudo systemctl status job-scraper.service

# View logs
sudo journalctl -u job-scraper.service -n 50

# Disable auto-start on boot
sudo systemctl disable job-scraper.service

# Enable auto-start on boot
sudo systemctl enable job-scraper.service
```

## Troubleshooting

### Service fails to start

1. Check the service status:
   ```bash
   sudo systemctl status job-scraper.service
   ```

2. Check logs:
   ```bash
   sudo journalctl -u job-scraper.service -n 100
   ```

3. Verify paths are correct in the service file

4. Ensure Python and dependencies are installed correctly

5. Check file permissions:
   ```bash
   ls -la /path/to/scraper_jobs/
   ```

### Script runs but no jobs found

- The job boards' HTML structure may have changed
- Check the application log: `tail -f job_scraper.log`
- You may need to update the scraper selectors in `main.py`

### Discord webhook not working

1. Verify the webhook URL is correct
2. Test the webhook manually:
   ```bash
   curl -X POST "YOUR_WEBHOOK_URL" \
     -H "Content-Type: application/json" \
     -d '{"content": "Test message"}'
   ```

### Permission errors

- Ensure the user in the service file has read/write permissions
- Check log directory permissions:
  ```bash
  sudo chown -R YOUR_USERNAME:YOUR_USERNAME /var/log/job-scraper
  ```

## Updating the Script

When you update `main.py`:

```bash
# Stop the service
sudo systemctl stop job-scraper.service

# Make your changes to main.py

# Restart the service
sudo systemctl start job-scraper.service
```

## Notes

- The script runs immediately on start, then schedules daily runs at 09:00 AM
- You can change the schedule time in `main.py` by modifying: `schedule.every().day.at("09:00")`
- The script includes error handling so one scraper failure won't stop the others
- All jobs are logged to `job_scraper.log` in the project directory

