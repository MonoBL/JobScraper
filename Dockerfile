# syntax=docker/dockerfile:1

FROM node:22-bookworm-slim AS webbuilder
WORKDIR /web
COPY web/package.json web/package-lock.json ./
RUN npm ci
COPY web/ ./
RUN npm run build

FROM mcr.microsoft.com/playwright/python:v1.49.1-noble AS app
WORKDIR /app
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    JOB_SCRAPER_STATE_DIR=/var/lib/job-scraper

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .
COPY --from=webbuilder /web/dist ./web/dist

RUN mkdir -p /var/lib/job-scraper/data

EXPOSE 8765

CMD ["python", "-m", "uvicorn", "api.app:app", "--host", "0.0.0.0", "--port", "8765"]
