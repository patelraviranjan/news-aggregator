"""Blueprint registry. Imported by application factory."""
from app_modules.extensions import db, login_manager, bcrypt, jwt, cors, limiter, talisman, cache

__all__ = [
    "db", "login_manager", "bcrypt", "jwt", "cors",
    "limiter", "talisman", "cache",
]
