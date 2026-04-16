# Job Scraper – Websites & Wish List

This document lists all websites the bot scrapes and all role titles used for ranking (wish list).

---

## Websites Being Scraped

### Crypto / Web3

| Source | URL | Notes |
|--------|-----|--------|
| **Web3.career** | https://web3.career/remote-jobs | Remote jobs page |
| **CryptoJobsList.com** | https://cryptojobslist.com | |
| **CryptocurrencyJobs.co** | https://cryptocurrencyjobs.co | |
| **CryptoJobs.com** | https://www.cryptojobs.com/jobs | |
| **FindCryptoJobs.xyz** | https://www.findcryptojobs.xyz | Aggregator |
| **Bondex Network** | https://network.bondex.app/jobs?search=&location= | |
| **RemoteOK** | https://remoteok.com/remote-crypto-jobs | Crypto tag filter |
| **Wellfound** | https://wellfound.com/role/r/web3-engineer | Web3 engineer role search |
| **Delphi Ventures** | https://jobs.delphiventures.io/jobs?filter=... (IT) | Getro board, IT filter |
| **Telegram Channels** | Multiple channels | job_crypto_eu, web3hiring, degencryptojobs, cryptojobslist |

### Telegram channels (under “Telegram Channels”)

- https://t.me/s/job_crypto_eu  
- https://t.me/s/web3hiring  
- https://t.me/s/degencryptojobs  
- https://t.me/s/cryptojobslist  

### Currently disabled

| Source | URL | Reason |
|--------|-----|--------|
| **Solana Jobs** | https://jobs.solana.com/jobs?filter=... (IT) | Site blocks scrapers (403 + download triggers) |

### Cruise / Maritime IT

| Source | URL | Notes |
|--------|-----|--------|
| **Carnival Ship Jobs** | https://shipjobs.carnival.com/search?q= | IT-related only |
| **AllCruiseJobs.com** | https://www.allcruisejobs.com/it-jobs/ | IT jobs page |
| **Selection Partners** | https://selectionpartners.net/jobs/ | IT positions only |
| **PeopleConquest** | https://www.peopleconquest.com/jobs/ | Informática / IT |
| **Douro Azul** | https://www.douroazul.com/oportunidades/?_sft_area-funcao=information-technology | IT filter |

---

## Wish List – Roles (Crypto / Web3)

Ranking uses **JobRanker** in `main.py`. Jobs are matched by title (with seniority stripped) and keywords in title/description.

### Perfect match (core role titles)

- devops / devops engineer  
- sysadmin / system administrator / systems administrator  
- it systems administrator / it system administrator / it administrator  
- l2 support / level 2 support  
- infrastructure engineer  
- node operator / node operations  
- site reliability engineer / sre / sre engineer  
- platform engineer  
- cloud engineer  
- linux engineer / linux administrator  

**Perfect-match keywords (must combine with title):**

- **Linux:** linux, ubuntu, debian, centos  
- **Scripting:** python, bash, shell scripting, shell script  
- **Infrastructure:** kubernetes, docker, terraform, ansible, ci/cd, infrastructure, cloud, aws, gcp, azure  

### Good match

- it support / technical support  
- datacenter technician  
- it operations / operations engineer / support engineer  
- customer support engineer / technical support engineer  
- solutions architect / systems architect / technical architect  
- infrastructure architect / cloud architect  

**Good-match keywords:** hardware, repair, network, networking, tickets, on-site, onsite, equipment, server maintenance, troubleshooting, monitoring, alerting, incident response  

### Seniority prefixes (ignored when matching)

junior, mid, mid-level, senior, sr, lead, staff, principal, chief  

### Blacklist (titles / keywords)

Titles: senior solidity developer, marketing manager, sales manager, hr manager, legal counsel, lawyer, attorney, cfo, cto, founder, co-founder, product manager, product owner, ui/ux designer, content writer, copywriter, community manager, social media manager, influencer, accountant, finance manager, affiliate manager, business development, bd manager, legal admin, senior associate, analyst, client insights, sales analytics, product control, business operations, strategy manager, operations manager, internal audit, professional practices, compliance, regulatory  

Keywords: senior solidity, marketing manager, sales manager, hr manager, legal counsel, 10+ years experience, 15+ years, phd required, masters required, bachelor's degree required, affiliate, business development, bd, legal, compliance, audit, accounting, finance, analyst, sales analytics, client insights, product control, strategy, operations manager  

---

## Wish List – Roles (Cruise / Maritime IT)

Ranking uses **CruiseJobRanker** in `main.py` for cruise scrapers only.

### Perfect match

- it officer  
- it systems manager / systems manager / it manager  
- senior it officer  
- staff it  
- it administrator  
- network engineer  
- electro-technical / eto / 2nd eto / 3rd eto  
- it support specialist  

### Good match

- it assistant / assistant it  
- it support / technical support  
- it administrator  
- jr it / junior it  
- sr it assistant  

### Weak match (IT keywords)

it, information technology, computer, network, systems, software, hardware, technical support, electro-technical, eto  

---

*Generated from `main.py` (JobRanker, CruiseJobRanker, and scraper classes).*
