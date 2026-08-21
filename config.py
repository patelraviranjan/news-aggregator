"""
Application configuration loaded from environment variables.
Exposes Development / Production / Testing configs.
"""
import os
from datetime import timedelta
from dotenv import load_dotenv

load_dotenv()


class BaseConfig:
    """Base settings shared by all environments."""

    SECRET_KEY = os.getenv("SECRET_KEY", "dev-secret-key-change-me")
    JWT_SECRET_KEY = os.getenv("JWT_SECRET_KEY", "dev-jwt-secret-change-me")
    JWT_ACCESS_TOKEN_EXPIRES = timedelta(days=7)

    # SQLAlchemy 2.x/psycopg2 reject the "postgres://" scheme that Neon,
    # old Heroku-style providers, etc. hand out by default -- it has to be
    # "postgresql://". Normalize it here so pasting a connection string
    # straight from the provider's dashboard into DATABASE_URL just works
    # instead of crashing on boot with "Can't load plugin: ...postgres".
    _db_url = os.getenv("DATABASE_URL", "sqlite:///news.db")
    if _db_url.startswith("postgres://"):
        _db_url = _db_url.replace("postgres://", "postgresql://", 1)
    SQLALCHEMY_DATABASE_URI = _db_url
    SQLALCHEMY_TRACK_MODIFICATIONS = False

    # Password hashing cost — 11 rounds keeps login/register comfortably
    # under half a second while still being safely slow against brute-force.
    BCRYPT_LOG_ROUNDS = int(os.getenv("BCRYPT_LOG_ROUNDS", 11))

    # Flask-Caching
    CACHE_TYPE = os.getenv("CACHE_TYPE", "SimpleCache")
    CACHE_DEFAULT_TIMEOUT = 300
    CACHE_KEY_PREFIX = "news_"

    # External APIs (optional)
    NEWSAPI_KEY = os.getenv("NEWSAPI_KEY", "")
    GNEWS_API_KEY = os.getenv("GNEWS_API_KEY", "")
    GOOGLE_CLIENT_ID = os.getenv("GOOGLE_CLIENT_ID", "")
    GOOGLE_CLIENT_SECRET = os.getenv("GOOGLE_CLIENT_SECRET", "")

    # Mail
    MAIL_SERVER = os.getenv("MAIL_SERVER", "smtp.gmail.com")
    MAIL_PORT = int(os.getenv("MAIL_PORT", 587))
    MAIL_USERNAME = os.getenv("MAIL_USERNAME", "")
    MAIL_PASSWORD = os.getenv("MAIL_PASSWORD", "")

    # Scheduler
    FETCH_INTERVAL_MINUTES = int(os.getenv("FETCH_INTERVAL_MINUTES", 1))

    # Vercel Cron -- see routes/api_routes.py:api_cron_fetch and vercel.json.
    # Vercel auto-populates this as the Authorization header on cron-triggered
    # requests whenever a CRON_SECRET env var exists on the project; set the
    # same value locally only if you want to test the endpoint manually.
    CRON_SECRET = os.getenv("CRON_SECRET", "")
    CRON_TIME_BUDGET_SECONDS = (
        int(os.getenv("CRON_TIME_BUDGET_SECONDS"))
        if os.getenv("CRON_TIME_BUDGET_SECONDS") else None
    )

    # Uploads
    UPLOAD_FOLDER = os.path.join("static", "uploads")
    MAX_CONTENT_LENGTH = 5 * 1024 * 1024  # 5 MB profile pictures

    # Flask-Limiter
    RATELIMIT_STORAGE_URI = "memory://"
    RATELIMIT_DEFAULT = "200 per hour"

    # Pagination
    ARTICLES_PER_PAGE = 12

    # CORS
    CORS_ORIGINS = ["*"]


class DevelopmentConfig(BaseConfig):
    DEBUG = True
    TEMPLATES_AUTO_RELOAD = True
    EXPLAIN_TEMPLATE_LOADING = False


class ProductionConfig(BaseConfig):
    DEBUG = False
    # Only force HTTPS-only cookies/redirects when actually served over HTTPS
    # (e.g. behind a TLS-terminating proxy). Forcing this on plain HTTP setups
    # like http://127.0.0.1:5000 silently breaks login: the browser refuses to
    # store a "Secure" cookie over HTTP, so the session never persists.
    FORCE_HTTPS = os.getenv("FORCE_HTTPS", "false").lower() == "true"
    SESSION_COOKIE_SECURE = FORCE_HTTPS
    REMEMBER_COOKIE_SECURE = FORCE_HTTPS


class TestingConfig(BaseConfig):
    TESTING = True
    SQLALCHEMY_DATABASE_URI = "sqlite:///:memory:"
    CACHE_TYPE = "NullCache"


def get_config():
    """Pick a config based on FLASK_ENV."""
    env = os.getenv("FLASK_ENV", "development").lower()
    return {
        "development": DevelopmentConfig,
        "production": ProductionConfig,
        "testing": TestingConfig,
    }.get(env, DevelopmentConfig)
