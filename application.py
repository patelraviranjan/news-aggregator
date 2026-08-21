"""Application factory — wires extensions, blueprints, security, scheduler."""
import os
import logging
from flask import Flask, render_template, request, redirect, url_for
from flask_login import current_user
from config import get_config
from app_modules.extensions import db, login_manager, bcrypt, jwt, cors, limiter, talisman, cache
from app_modules.security import generate_csrf_token
from app_modules.models import User


def create_app() -> Flask:
    # On Vercel the deployed code lives on a read-only filesystem, so
    # Flask's default instance folder (<app_root>/instance) can't be
    # created and db.init_app() below crashes with
    # "OSError: [Errno 30] Read-only file system". Only /tmp is writable
    # in that environment, so redirect the instance path there whenever
    # we're running on Vercel. Locally (no VERCEL env var) this keeps
    # using the normal ./instance folder next to the app.
    if os.environ.get("VERCEL"):
        instance_path = "/tmp/instance"
        os.makedirs(instance_path, exist_ok=True)
        app = Flask(__name__, static_folder="static", template_folder="templates",
                    instance_relative_config=True, instance_path=instance_path)
    else:
        app = Flask(__name__, static_folder="static", template_folder="templates")

    cfg = get_config()
    app.config.from_object(cfg)

    # ---------- Logging ----------
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")

    # ---------- Extensions ----------
    db.init_app(app)
    bcrypt.init_app(app)
    jwt.init_app(app)
    cache.init_app(app)
    cors.init_app(app, resources={r"/api/*": {"origins": "*"}})
    login_manager.init_app(app)
    login_manager.login_view = "auth.login"
    login_manager.login_message_category = "info"

    @login_manager.user_loader
    def load_user(uid):
        from app_modules.models import Admin
        if isinstance(uid, str) and uid.startswith("admin-"):
            return db.session.get(Admin, int(uid.split("-", 1)[1]))
        return db.session.get(User, int(uid))

    # ---------- Rate Limiter ----------
    limiter.init_app(app)

    # ---------- Security Headers (HTTPS in prod, permissive CSP in dev) ----------
    if app.config.get("DEBUG") or app.config.get("TESTING"):
        talisman.init_app(
            app,
            force_https=False,
            strict_transport_security=False,
            content_security_policy={
                "default-src": "'self'",
                "img-src": ["'self'", "data:", "https:"],
                "script-src": ["'self'", "'unsafe-inline'", "https:", "http:"],
                "style-src": ["'self'", "'unsafe-inline'", "https:", "http:"],
                "font-src": ["'self'", "data:", "https:"],
                "connect-src": ["'self'", "https:"],
            },
        )
    else:
        talisman.init_app(app, force_https=app.config.get("FORCE_HTTPS", False))

    # ---------- CSRF ----------
    @app.context_processor
    def inject_csrf():
        return {"csrf_token": generate_csrf_token}

    # ---------- Jinja Globals ----------
    @app.context_processor
    def inject_globals():
        from datetime import date, timedelta
        from app_modules.models import Category, Notification
        from flask import url_for
        try:
            cats = Category.query.filter_by(is_active=True).order_by(Category.sort_order).all()
        except Exception:
            cats = []
        unread_count = 0
        if current_user.is_authenticated:
            try:
                unread_count = Notification.query.filter_by(user_id=current_user.id, is_read=False).count()
            except Exception:
                unread_count = 0
        _today = date.today()

        # Surface the SQLite-on-Vercel misconfiguration directly on every
        # page (not just /api/status) -- an empty "No articles fetched
        # yet" feed with no visible error looks identical to a real bug,
        # so make the actual cause impossible to miss for whoever's
        # looking at the site. Checks the same env var names config.py
        # falls back through (Vercel's own Postgres integration doesn't
        # use the name "DATABASE_URL").
        db_misconfig_warning = (
            db.engine.dialect.name == "sqlite"
            and bool(os.environ.get("VERCEL"))
            and not any(os.environ.get(name) for name in
                        ("DATABASE_URL", "POSTGRES_URL",
                         "POSTGRES_PRISMA_URL", "POSTGRES_URL_NON_POOLING"))
        )

        def proxy_img(url):
            """Route a remote article image through our own caching proxy so
            hotlink-protected publishers (many Indian/Nepali news sites block
            direct <img> requests from other domains) still render, and so
            repeat views are served from local cache instead of re-hitting
            the publisher every time."""
            if not url:
                return ""
            return url_for("public.image_proxy", u=url)

        return {
            "categories": cats,
            "unread_notifications": unread_count,
            "site_name": "News Aggregator",
            "current_year": _today.year,
            "today": _today,
            "yesterday": _today - timedelta(days=1),
            "proxy_img": proxy_img,
            "db_misconfig_warning": db_misconfig_warning,
        }

    # ---------- Blueprints ----------
    from routes.public_routes import public_bp
    from routes.auth_routes import auth_bp
    from routes.news_routes import news_bp
    from routes.user_routes import user_bp
    from routes.admin_routes import admin_bp
    from routes.api_routes import api_bp

    app.register_blueprint(public_bp)
    app.register_blueprint(auth_bp, url_prefix="/auth")
    app.register_blueprint(news_bp, url_prefix="/news")
    app.register_blueprint(user_bp, url_prefix="/user")
    app.register_blueprint(admin_bp, url_prefix="/admin")
    app.register_blueprint(api_bp, url_prefix="/api")

    # ---------- Error handlers ----------
    @app.errorhandler(404)
    def not_found(e):
        if request.path.startswith("/api/"):
            return {"error": "Not found", "status": 404}, 404
        return render_template("errors/404.html"), 404

    @app.errorhandler(500)
    def server_error(e):
        db.session.rollback()
        if request.path.startswith("/api/"):
            return {"error": "Server error", "status": 500}, 500
        return render_template("errors/500.html"), 500

    @app.errorhandler(429)
    def ratelimit_error(e):
        if request.path.startswith("/api/"):
            return {"error": "Too many requests", "status": 429}, 429
        return render_template("errors/404.html", message="Too many requests"), 429

    # ---------- Self-healing feed (Vercel has no background scheduler) ----------
    # /api/cron/fetch (driven by vercel.json's cron) is the "proper" way
    # articles get fetched on Vercel, but that depends on: (a) the deploy
    # actually shipping vercel.json's cron entry, (b) DATABASE_URL already
    # being Postgres before the first cron tick, and (c) waiting for the
    # schedule to fire (once/day on the Hobby plan). Any one of those being
    # off looks identical from the outside: an empty "No articles fetched
    # yet" homepage with no error on screen. Rather than depend on all three
    # lining up, run one fetch cycle inline on the first real page view if
    # the articles table is still empty, so the feed fills itself in as soon
    # as someone actually visits the site -- cron then just keeps it fresh.
    # A cache-backed lock caps this to at most one attempt every 2 minutes
    # so concurrent visitors don't all trigger it at once, and it's a no-op
    # the instant any article exists.
    @app.before_request
    def _self_heal_empty_feed():
        if request.method != "GET":
            return
        if request.path.startswith(("/api/", "/static/", "/media/")):
            return
        try:
            from app_modules.models import Article
            if Article.query.first() is not None:
                return  # feed already has data -- nothing to do
        except Exception:
            return  # table not ready / DB unreachable -- let the normal page render and show its own error

        lock_key = "self_heal_fetch_lock"
        if cache.get(lock_key):
            return
        cache.set(lock_key, True, timeout=120)

        budget = app.config.get("CRON_TIME_BUDGET_SECONDS")
        if budget is None and os.environ.get("VERCEL"):
            budget = 8  # stay under Vercel's 10s Hobby-plan function timeout
        try:
            from scheduler import run_fetch_cycle
            result = run_fetch_cycle(app, max_seconds=budget)
            app.logger.info("Self-heal fetch on empty feed: %s", result)
        except Exception as ex:
            app.logger.warning("Self-heal fetch failed: %s", ex)

    # ---------- Bootstrap DB + admin + scheduler ----------
    with app.app_context():
        # SQLite only allows one writer at a time. Gunicorn runs multiple
        # worker processes (see Dockerfile: `-w 3`), and each worker's own
        # background scheduler was writing fetched articles to the *same*
        # news.db file every interval. When two workers' jobs landed close
        # together one would hit "database is locked" -- and persist()
        # swallows that error (just logs a warning), so it failed silently
        # and articles simply stopped landing. This is why the site looked
        # frozen on yesterday's news with no visible error anywhere.
        # WAL mode lets reads/writes overlap and busy_timeout makes a
        # blocked writer retry instead of failing immediately.
        if app.config["SQLALCHEMY_DATABASE_URI"].startswith("sqlite"):
            from sqlalchemy import event

            @event.listens_for(db.engine, "connect")
            def _set_sqlite_pragmas(dbapi_connection, _):
                cur = dbapi_connection.cursor()
                cur.execute("PRAGMA journal_mode=WAL")
                cur.execute("PRAGMA busy_timeout=30000")
                cur.close()

        # On Vercel each request can land on a different, freshly-booted
        # serverless instance, and even repeat requests to "the same"
        # instance don't keep /tmp around forever (it's wiped on redeploy
        # and reclaimed between cold starts). A SQLite file living in /tmp
        # therefore isn't shared between the web request that reads
        # articles and the cron request that fetched them -- so the site
        # deploys fine, /api/cron/fetch reports success, and the feed still
        # shows "No articles fetched yet" forever, with nothing that looks
        # like an error anywhere. This is the actual root cause of that
        # symptom, so make it loud instead of a silent empty feed.
        if os.environ.get("VERCEL") and app.config["SQLALCHEMY_DATABASE_URI"].startswith("sqlite"):
            app.logger.warning(
                "Running on Vercel with a SQLite DATABASE_URL. Articles fetched "
                "by /api/cron/fetch will NOT reliably show up on the site -- "
                "SQLite on Vercel's serverless filesystem isn't persistent or "
                "shared across function instances. Set DATABASE_URL to a real "
                "Postgres database (Vercel Postgres, Neon, Supabase, etc.)."
            )
            print("\n*** WARNING: SQLite on Vercel is not persistent -- the feed "
                  "will stay empty until DATABASE_URL points at Postgres. ***\n")

        db.create_all()
        _seed_defaults()

        # Background RSS scheduler (skip during tests, and skip on Vercel --
        # serverless functions don't keep a background process alive, so an
        # in-process APScheduler job here will silently never fire after
        # this one cold-start invocation ends. Use Vercel Cron hitting a
        # dedicated /api/fetch endpoint instead.)
        if not app.config.get("TESTING") and not os.environ.get("VERCEL"):
            try:
                from scheduler import start_scheduler
                start_scheduler(app)
            except Exception as ex:
                # This used to only go to app.logger, which is easy to miss
                # in a console -- and a swallowed exception here silently
                # means "no articles are ever fetched," with nothing on
                # screen to explain why. Print loudly too.
                app.logger.warning("Scheduler not started: %s", ex)
                print(f"\n*** WARNING: background news scheduler failed to start: {ex}\n"
                      f"*** No news will be fetched automatically until this is fixed.\n")

    @app.after_request
    def add_security_headers(resp):
        resp.headers["X-Content-Type-Options"] = "nosniff"
        resp.headers["X-Frame-Options"] = "SAMEORIGIN"
        resp.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        return resp

    return app


def _seed_defaults():
    """Seed categories, default sources and admin on first boot."""
    from app_modules.models import Category, Source, Admin

    # Categories
    defaults = [
        ("technology", "Technology", "bi-cpu", "#3b82f6", 1),
        ("business",    "Business",    "bi-briefcase", "#10b981", 2),
        ("sports",      "Sports",      "bi-trophy", "#ef4444", 3),
        ("politics",    "Politics",    "bi-flag", "#8b5cf6", 4),
        ("entertainment","Entertainment","bi-film", "#ec4899", 5),
        ("health",      "Health",      "bi-heart-pulse", "#f97316", 6),
        ("science",     "Science",     "bi-rocket-takeoff", "#06b6d4", 7),
        ("world",       "World",       "bi-globe2", "#0ea5e9", 8),
        ("local",       "Local",       "bi-geo-alt", "#22c55e", 9),
    ]
    for slug, name, icon, color, order in defaults:
        if not Category.query.filter_by(slug=slug).first():
            db.session.add(Category(slug=slug, name=name, icon=icon,
                                    color=color, sort_order=order,
                                    description=f"Latest {name.lower()} news"))
    db.session.commit()

    # Default sources (RSS)
    rss_defaults = [
        ("BBC News",     "https://feeds.bbci.co.uk/news/rss.xml",    "global"),
        ("NY Times",     "https://rss.nytimes.com/services/xml/rss/nyt/HomePage.xml", "us"),
        ("The Guardian", "https://www.theguardian.com/world/rss",   "global"),
        ("Al Jazeera",   "https://www.aljazeera.com/xml/rss/all.xml", "global"),
        ("OnlineKhabar", "https://www.onlinekhabar.com/feed",        "np"),
        ("NPR",          "https://feeds.npr.org/1001/rss.xml",       "us"),
        ("CNN",          "http://rss.cnn.com/rss/cnn_topstories.rss", "us"),
        ("Aaj Tak",      "https://www.aajtak.in/rssfeeds/?id=home",  "in"),
        ("Hindustan Times", "https://www.hindustantimes.com/feeds/rss/latest/rssfeed.xml", "in"),
        ("Zee News",     "https://zeenews.india.com/rss/india-national-news.xml", "in"),
    ]
    for name, rss, country in rss_defaults:
        if not Source.query.filter_by(name=name).first():
            db.session.add(Source(name=name, url="https://"+name.replace(" ", "").lower(),
                                  rss_url=rss, country=country))
    db.session.commit()

    # Default admin
    if not Admin.query.filter_by(email="patelbhai0096@gmail.com").first():
        a = Admin(email="patelbhai0096@gmail.com", name="Super Admin", role="superadmin")
        a.set_password("Admin@123")
        db.session.add(a)
        db.session.commit()

    # NOTE: articles are intentionally never hardcoded/seeded here. Every
    # article on the site comes from the real RSS auto-fetch pipeline
    # (services/news_fetcher.py) -- the background scheduler runs its first
    # cycle ~10 seconds after boot (see scheduler.py) and pulls live
    # articles + images from the sources seeded above. The site may show an
    # empty state for those first ~10 seconds on a brand-new database; that
    # is expected and preferable to seeding fake demo content that never
    # gets replaced by real feed data.