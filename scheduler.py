"""APScheduler wrapper: refresh news from RSS + (optional) APIs every N minutes."""
import logging
import os
import sys
import tempfile
from datetime import datetime, timedelta
from apscheduler.schedulers.background import BackgroundScheduler

log = logging.getLogger(__name__)

# gunicorn is started with multiple worker processes (-w 3 in the
# Dockerfile). create_app() runs once per worker, so without this guard
# every worker would boot its own BackgroundScheduler and all of them
# would fetch + write to the same database on the same interval --
# tripling the load and (on SQLite) causing write-lock contention that
# silently dropped article inserts. Only the first process to grab this
# lock file actually starts the scheduler; others skip it and just
# serve requests against whatever that one process fetches.
#
# fcntl (Unix) and msvcrt (Windows) are both stdlib but mutually
# exclusive -- importing the wrong one raises ImportError, which used to
# propagate straight up and get silently swallowed by application.py's
# "Scheduler not started" try/except on Windows, so the scheduler never
# ran at all and no article was ever fetched. Pick whichever is actually
# available on this OS instead of hard-importing fcntl.
if sys.platform == "win32":
    import msvcrt
else:
    import fcntl

_LOCK_PATH = os.path.join(tempfile.gettempdir(), "news_aggregator_scheduler.lock")


def _acquire_singleton_lock():
    fd = open(_LOCK_PATH, "w")
    try:
        if sys.platform == "win32":
            # msvcrt.locking locks a byte range starting at the file's
            # current position -- needs at least 1 byte present first,
            # or the lock call is unreliable on an empty file.
            fd.write("1")
            fd.flush()
            fd.seek(0)
            msvcrt.locking(fd.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        fd.close()
        return False
    # Keep the fd open (and referenced) for the lifetime of the process --
    # closing it releases the lock, and if garbage collected the OS also
    # releases it, so stash it somewhere that outlives this function.
    _acquire_singleton_lock._held_fd = fd
    return True


def start_scheduler(app):
    if not _acquire_singleton_lock():
        log.info("Scheduler already running in another worker process; skipping here.")
        return None

    interval = app.config.get("FETCH_INTERVAL_MINUTES", 15)
    sched = BackgroundScheduler(daemon=True)

    def job():
        with app.app_context():
            from app_modules.extensions import db
            from app_modules.models import Source
            from services.news_fetcher import fetch_rss, fetch_newsapi, fetch_gnews, persist

            sources = Source.query.filter_by(is_active=True).all()
            inserted_total = 0
            for s in sources:
                if not s.rss_url:
                    continue
                try:
                    # limit=None pulls every entry the feed currently offers
                    # (same as the manual "Fetch now" button) instead of
                    # capping at 20/cycle -- persist() already dedupes via
                    # content-hash slugs, so re-fetching the full feed each
                    # cycle only ever inserts what's actually new.
                    items = list(fetch_rss(s.rss_url, s.name, limit=None))
                    # persist()'s default max_image_enrich=3 was written for
                    # a single manual add, not a recurring auto-fetch --
                    # left at 3, every cycle only ever gave 3 of that
                    # source's new articles a real image (one og:image
                    # scrape per missing image), so headline/card lists
                    # were mostly showing the no-photo placeholder icon
                    # instead of actual thumbnails. Match the same cap the
                    # manual "Fetch now" / "Add source" actions use.
                    inserted_total += persist(app, items, source_model=s,
                                               max_image_enrich=min(len(items), 25))
                except Exception as ex:
                    log.warning("Source %s failed: %s", s.name, ex)

            # optional external APIs
            if app.config.get("NEWSAPI_KEY"):
                for cat in ("technology", "business", "sports", "world"):
                    items = list(fetch_newsapi(app.config["NEWSAPI_KEY"], category=cat, limit=100))
                    inserted_total += persist(app, items, category_slug=cat,
                                               max_image_enrich=min(len(items), 25))
            if app.config.get("GNEWS_API_KEY"):
                items = list(fetch_gnews(app.config["GNEWS_API_KEY"], limit=100))
                inserted_total += persist(app, items, max_image_enrich=min(len(items), 25))

            # update trending
            try:
                from services.trending_service import recalc_trending
                recalc_trending()
            except Exception:
                pass
            log.info("Scheduler cycle complete: %d new articles cached.", inserted_total)

    # Bug fix: next_run_time=None left the job permanently paused with no
    # scheduled fire time, so news never actually auto-refreshed. Run once
    # shortly after boot (so the site isn't stale on startup) and then every
    # `interval` minutes after that.
    sched.add_job(job, "interval", minutes=interval, id="refresh_news",
                  replace_existing=True, max_instances=1, coalesce=True,
                  misfire_grace_time=30,
                  next_run_time=datetime.now() + timedelta(seconds=10))
    sched.start()
    log.info("APScheduler started [interval=%d min].", interval)
    return sched
