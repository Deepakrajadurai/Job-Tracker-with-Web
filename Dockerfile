# Job Tracker web app and collector in one image.
#   docker compose up -d --build
FROM python:3.15-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app \
    PLAYWRIGHT_BROWSERS_PATH=/ms-playwright \
    JOBTRACKER_HOME=/srv/jobtracker

# tzdata so the 04:00 schedule follows TZ (e.g. Europe/Berlin) instead of UTC
RUN apt-get update && apt-get install -y --no-install-recommends tzdata \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Optional Method B (browser rendering), about 400 MB more:
#   docker compose build --build-arg INSTALL_BROWSER=1
ARG INSTALL_BROWSER=0
RUN if [ "$INSTALL_BROWSER" = "1" ]; then \
      pip install --no-cache-dir playwright && playwright install --with-deps chromium \
      && chmod -R a+rx /ms-playwright; \
    fi

COPY jobtracker ./jobtracker
COPY config.example.yaml ./

ARG APP_VERSION=dev
ENV JOBTRACKER_VERSION=$APP_VERSION

WORKDIR /srv/jobtracker
HEALTHCHECK --interval=60s --timeout=5s --start-period=20s \
  CMD python -c "import os,urllib.request; urllib.request.urlopen(f\"http://127.0.0.1:{os.environ.get('JOBTRACKER_PORT','8000')}/healthz\", timeout=4)"
CMD ["python", "-m", "jobtracker.web"]
