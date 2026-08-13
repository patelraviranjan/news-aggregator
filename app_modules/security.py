"""Security helpers — CSRF token, sanitisation, common guards."""
import secrets
from functools import wraps
from flask import session, abort, request
import bleach


def generate_csrf_token() -> str:
    """Issue / fetch CSRF token. Stored in Flask session."""
    if "_csrf_token" not in session:
        session["_csrf_token"] = secrets.token_urlsafe(32)
    return session["_csrf_token"]


def validate_csrf():
    if request.method in ("POST", "PUT", "PATCH", "DELETE"):
        token = session.get("_csrf_token")
        sent = request.form.get("csrf_token") or request.headers.get("X-CSRF-Token")
        if not token or token != sent:
            if request.is_json:
                abort(400, description="Invalid or missing CSRF token")
            from flask import flash, redirect
            flash("Your session expired. Please try again.", "warning")
            return redirect(request.referrer or "/")


def csrf_required(view):
    @wraps(view)
    def wrapper(*args, **kwargs):
        result = validate_csrf()
        if result is not None:
            return result
        return view(*args, **kwargs)
    return wrapper


def sanitize_html(raw: str) -> str:
    """Strip harmful tags/attrs from user-submitted content."""
    if not raw:
        return ""
    return bleach.clean(
        raw,
        tags=["b", "i", "u", "strong", "em", "p", "br", "ul", "ol", "li", "a", "blockquote", "code"],
        attributes={"a": ["href", "title", "rel"]},
        strip=True,
    )


def admin_required(view):
    """Block non-admin access."""
    from flask import redirect, url_for
    from flask_login import current_user
    from app_modules.models import Admin

    @wraps(view)
    def wrapper(*args, **kwargs):
        if not current_user.is_authenticated:
            return redirect(url_for("auth.admin_login"))
        if not isinstance(current_user, Admin):
            return redirect(url_for("public.home"))
        return view(*args, **kwargs)
    return wrapper
