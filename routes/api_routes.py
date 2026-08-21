"""JSON REST API endpoints documented in the spec."""
import os
from flask import Blueprint, request, jsonify, abort, current_app
from flask_jwt_extended import create_access_token, jwt_required, get_jwt_identity
from flask_login import current_user, login_required
from sqlalchemy import desc, or_
from app_modules.extensions import db, bcrypt
from app_modules.models import User, Article, Category, Bookmark, Source
from app_modules.security import sanitize_html, csrf_required

api_bp = Blueprint("api", __name__)


# ---- Diagnostics ----

@api_bp.route("/status")
def api_status():
    """No-secret diagnostic endpoint -- open this URL in a browser to see
    exactly why the feed is/isn't showing, instead of guessing. Doesn't
    expose the CRON_SECRET value itself, only whether one is set.
    """
    from app_modules.models import Article, Source
    from sqlalchemy import desc as _desc

    dialect = db.engine.dialect.name
    sqlite_on_vercel = dialect == "sqlite" and bool(os.environ.get("VERCEL"))
    try:
        total_articles = Article.query.count()
        latest = Article.query.order_by(_desc(Article.published_at)).first()
        active_sources = Source.query.filter_by(is_active=True).count()
        db_error = None
    except Exception as ex:
        total_articles = None
        latest = None
        active_sources = None
        db_error = str(ex)

    return jsonify({
        "db_dialect": dialect,
        "db_error": db_error,
        "sqlite_on_vercel_warning": (
            "DATABASE_URL is SQLite while running on Vercel -- this WILL "
            "NOT persist between requests. Set DATABASE_URL to a Postgres "
            "connection string (Neon/Supabase/Vercel Postgres) and redeploy."
            if sqlite_on_vercel else None
        ),
        "article_count": total_articles,
        "active_sources": active_sources,
        "latest_article_at": latest.published_at.isoformat() if latest and latest.published_at else None,
        "cron_secret_configured": bool(current_app.config.get("CRON_SECRET")),
        "running_on_vercel": bool(os.environ.get("VERCEL")),
    })


# ---- Cron (Vercel) ----

@api_bp.route("/cron/fetch", methods=["GET", "POST"])
def api_cron_fetch():
    """Runs one RSS fetch cycle. This is what actually populates the feed
    on Vercel -- serverless functions don't keep a background process
    alive, so `scheduler.py`'s APScheduler loop never fires there
    (application.py skips starting it whenever the VERCEL env var is set).
    Instead, Vercel Cron (configured in vercel.json) sends an HTTP request
    to this endpoint on a schedule, and each request runs exactly one
    fetch cycle before the function exits.

    Protected by CRON_SECRET so randoms can't hit this URL and force
    fetch cycles: Vercel automatically sends
    `Authorization: Bearer <CRON_SECRET>` on cron-triggered requests once
    a CRON_SECRET env var exists on the project, so this only has to
    compare that header. A `?secret=` query param is also accepted so it
    can still be triggered manually (e.g. `curl`) for testing.
    """
    expected = current_app.config.get("CRON_SECRET")
    if expected:
        sent = request.headers.get("Authorization", "")
        sent = sent.split("Bearer ", 1)[-1] if "Bearer " in sent else sent
        if sent != expected and request.args.get("secret") != expected:
            return jsonify({"error": "unauthorized"}), 401
    else:
        current_app.logger.warning(
            "CRON_SECRET is not set -- /api/cron/fetch is running with no "
            "auth check. Set a CRON_SECRET env var in Vercel so only "
            "Vercel Cron (or someone who knows the secret) can trigger it."
        )

    from scheduler import run_fetch_cycle
    # Soft time budget so a slow/hanging source can't eat the whole
    # function timeout (10s on Vercel Hobby, 60s+ on Pro) and leave every
    # other source unfetched. Configurable via CRON_TIME_BUDGET_SECONDS if
    # the default doesn't fit your plan.
    budget = current_app.config.get("CRON_TIME_BUDGET_SECONDS")
    result = run_fetch_cycle(current_app._get_current_object(), max_seconds=budget)
    return jsonify({"ok": True, **result})


# ---- Auth ----

@api_bp.route("/register", methods=["POST"])
def api_register():
    data = request.get_json(force=True, silent=True) or {}
    if not data.get("username") or not data.get("email") or not data.get("password"):
        return jsonify({"error": "missing fields"}), 400
    if User.query.filter((User.username == data["username"]) | (User.email == data["email"])).first():
        return jsonify({"error": "user exists"}), 409
    u = User(username=data["username"], email=data["email"], full_name=data.get("full_name", ""))
    u.set_password(data["password"])
    db.session.add(u); db.session.commit()
    token = create_access_token(identity=u.username)
    return jsonify({"user": u.to_dict(), "access_token": token}), 201


@api_bp.route("/login", methods=["POST"])
def api_login():
    data = request.get_json(force=True, silent=True) or {}
    email = (data.get("email") or "").strip().lower()
    u = User.query.filter_by(email=email).first()
    if not u or not u.check_password(data.get("password", "")):
        return jsonify({"error": "invalid credentials"}), 401
    token = create_access_token(identity=u.username)
    return jsonify({"user": u.to_dict(), "access_token": token})


@api_bp.route("/google-login", methods=["POST"])
def api_google_login():
    """JSON API clients (mobile apps etc.) — verifies token and issues a JWT."""
    from flask import current_app
    from flask_login import login_user

    data = request.get_json(force=True, silent=True) or {}
    token = data.get("credential")
    if not token:
        return jsonify({"error": "missing credential"}), 400
    client_id = current_app.config.get("GOOGLE_CLIENT_ID")
    if not client_id:
        return jsonify({"error": "Google sign-in is not configured on this server."}), 501
    try:
        from google.oauth2 import id_token as google_id_token
        from google.auth.transport import requests as google_requests
        info = google_id_token.verify_oauth2_token(token, google_requests.Request(), client_id)
        if info.get("iss") not in ("accounts.google.com", "https://accounts.google.com"):
            raise ValueError("Wrong issuer.")
    except Exception as ex:
        current_app.logger.warning("Google token verification failed: %s", ex)
        return jsonify({"error": "Invalid or expired Google token."}), 401
    if not info.get("email_verified", False):
        return jsonify({"error": "Google account email is not verified."}), 401
    email = (info.get("email") or "").strip().lower()
    u = User.query.filter_by(email=email).first()
    if not u:
        import re, secrets as _secrets
        base_username = re.sub(r"[^a-zA-Z0-9_]", "", (info.get("given_name") or email.split("@")[0])).lower() or "user"
        username = base_username
        suffix = 1
        while User.query.filter_by(username=username).first():
            suffix += 1
            username = f"{base_username}{suffix}"
        u = User(username=username, email=email, full_name=info.get("name") or username,
                 email_verified=True, avatar=info.get("picture") or "images/default-avatar.png")
        u.set_password(_secrets.token_urlsafe(24))
        db.session.add(u); db.session.commit()
    login_user(u, remember=True)
    token_out = create_access_token(identity=u.username)
    return jsonify({"user": u.to_dict(), "access_token": token_out})


# ---- News (public) ----

@api_bp.route("/news")
def api_news():
    page = int(request.args.get("page", 1))
    per  = int(request.args.get("per_page", 20))
    return jsonify({
        "results": [a.to_dict() for a in
                    Article.query.order_by(desc(Article.published_at))
                                  .paginate(page=page, per_page=per).items]
    })


@api_bp.route("/news/latest")
def api_latest():
    return jsonify({"results": [a.to_dict() for a in
                                Article.query.order_by(desc(Article.published_at)).limit(20).all()]})


@api_bp.route("/news/trending")
def api_trending():
    return jsonify({"results": [a.to_dict() for a in
                                Article.query.filter_by(is_trending=True)
                                             .order_by(desc(Article.views))
                                             .limit(20).all()]})


@api_bp.route("/news/category/<category>")
def api_by_category(category):
    cat = Category.query.filter_by(slug=category).first()
    if not cat:
        return jsonify({"error": "category not found"}), 404
    return jsonify({"category": cat.name,
                    "results": [a.to_dict() for a in
                                Article.query.filter_by(category_id=cat.id)
                                             .order_by(desc(Article.published_at))
                                             .limit(40).all()]})


@api_bp.route("/news/search")
def api_search():
    q = request.args.get("q", "").strip()
    sort = request.args.get("sort", "newest")
    if not q:
        return jsonify({"results": []})
    query = Article.query.filter(or_(Article.title.ilike(f"%{q}%"),
                                    Article.summary.ilike(f"%{q}%")))
    if sort == "oldest":  query = query.order_by(Article.published_at)
    elif sort == "popular": query = query.order_by(desc(Article.views))
    else:                  query = query.order_by(desc(Article.published_at))
    return jsonify({"q": q, "results": [a.to_dict() for a in query.limit(40).all()]})


# ---- Personal ----

@api_bp.route("/bookmark", methods=["POST"])
@jwt_required()
def api_add_bookmark():
    uid = get_jwt_identity()
    u = User.query.filter_by(username=uid).first()
    aid = (request.get_json(silent=True) or {}).get("article_id")
    if not aid: return jsonify({"error": "article_id required"}), 400
    if not Bookmark.query.filter_by(user_id=u.id, article_id=aid).first():
        db.session.add(Bookmark(user_id=u.id, article_id=aid)); db.session.commit()
    return jsonify({"ok": True})


@api_bp.route("/bookmark")
@jwt_required()
def api_list_bookmarks():
    uid = get_jwt_identity()
    u = User.query.filter_by(username=uid).first()
    items = (db.session.query(Article).join(Bookmark, Bookmark.article_id == Article.id)
             .filter(Bookmark.user_id == u.id).all())
    return jsonify({"results": [a.to_dict() for a in items]})


@api_bp.route("/bookmark/<int:bid>", methods=["DELETE"])
@jwt_required()
def api_rm_bookmark(bid):
    b = Bookmark.query.get_or_404(bid); db.session.delete(b); db.session.commit()
    return jsonify({"ok": True})


@api_bp.route("/profile")
@jwt_required()
def api_profile():
    uid = get_jwt_identity()
    u = User.query.filter_by(username=uid).first()
    if not u:
        return jsonify({"error": "not found"}), 404
    return jsonify(u.to_dict())


@api_bp.route("/profile", methods=["PUT"])
@jwt_required()
def api_put_profile():
    uid = get_jwt_identity()
    u = User.query.filter_by(username=uid).first()
    if not u: return jsonify({"error": "not found"}), 404
    data = request.get_json(force=True, silent=True) or {}
    for f in ("full_name", "bio", "country", "language", "avatar"):
        if f in data: setattr(u, f, sanitize_html(str(data[f])))
    if "favorite_categories" in data and isinstance(data["favorite_categories"], list):
        u.favorite_categories = ",".join(data["favorite_categories"])
    db.session.commit()
    return jsonify(u.to_dict())


# ---- Admin (basic auth gate via JWT) ----

def _is_admin():
    from app_modules.models import Admin
    return current_user.is_authenticated and isinstance(current_user, Admin)


@api_bp.route("/admin/users")
@login_required
def api_admin_users():
    if not _is_admin(): abort(403)
    return jsonify({"results": [u.to_dict() for u in User.query.limit(200).all()]})


@api_bp.route("/admin/articles")
@login_required
def api_admin_articles():
    if not _is_admin(): abort(403)
    return jsonify({"results": [a.to_dict() for a in Article.query.order_by(Article.id.desc()).limit(200).all()]})


@api_bp.route("/admin/source", methods=["POST"])
@login_required
@csrf_required
def api_admin_source():
    if not _is_admin(): abort(403)
    data = request.get_json(force=True, silent=True) or request.form
    s = Source(name=data.get("name"), url=data.get("url", ""),
               rss_url=data.get("rss_url", ""),
               country=data.get("country", "global"))
    db.session.add(s); db.session.commit()
    return jsonify({"ok": True, "id": s.id}), 201
