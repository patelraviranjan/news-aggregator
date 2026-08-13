FROM python:3.11-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# System deps for Pillow / psycopg2
RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential libpq-dev curl \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt requirements-postgres.txt ./
RUN pip install --upgrade pip && pip install -r requirements-postgres.txt

COPY . .

EXPOSE 5000

# Use gunicorn in production, fall back to flask dev server if not installed.
# --timeout 90: manual "add/edit source" and "fetch now" admin actions can
# do a real feed pull plus up to 25 og:image lookups (photo enrichment) in
# one request; the default 30s gunicorn timeout could kill that worker
# mid-fetch on a slow publisher.
# NOTE: use "app:app" (the already-built object from app.py), NOT
# "app:create_app()". app.py calls create_app() once at import time to
# expose `app`; if gunicorn ALSO calls create_app() itself via the
# factory-call syntax, create_app() runs twice per worker. The scheduler's
# singleton lock (scheduler.py) is then grabbed by the *first* (import-time)
# instance, which gets discarded, while the *second* instance -- the one
# gunicorn actually serves requests with -- fails to acquire the lock and
# never starts its own scheduler. Net effect: auto-fetch silently never
# reaches the app users/admins actually hit; only manual "Fetch now"
# (which runs in-request on the real serving app) appears to work.
CMD ["gunicorn", "-w", "3", "-b", "0.0.0.0:5000", "--timeout", "90", "app:app"]
