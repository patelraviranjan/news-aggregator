"""Public pages: home, breaking, trending, categories, search, legal, errors."""
import hashlib
import logging
import os
from datetime import datetime, timedelta
from urllib.parse import urlparse

import requests
from flask import Blueprint, render_template, request, abort, current_app, Response, send_file
from sqlalchemy import desc, or_, func
from app_modules.extensions import db, cache
from app_modules.models import Article, Category, Source

public_bp = Blueprint("public", __name__)
log = logging.getLogger(__name__)

_IMG_CACHE_DIR = os.path.join("static", "uploads", "img_cache")
_IMG_HEADERS = {
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/124.0.0.0 Safari/537.36"),
    "Accept": "image/avif,image/webp,image/apng,image/*,*/*;q=0.8",
}


@public_bp.route("/favicon.ico")
def favicon_ico():
    """Serve the root-level /favicon.ico directly. Browsers request this
    exact path on their own, independent of any <link rel="icon"> tag in
    <head>, so relying on the static file only being reachable at
    /static/favicon.ico left every first page load logging a 404."""
    return current_app.send_static_file("favicon.ico")


@public_bp.route("/favicon.png")
def favicon_png():
    """Same as favicon.ico above, for clients that request /favicon.png."""
    return current_app.send_static_file("favicon.png")


@public_bp.route("/media/img")
def image_proxy():
    """Fetch + cache a remote article image on our own domain.

    Many publishers (common across Indian/Nepali news sites) reject <img>
    requests whose Referer isn't their own homepage, so hot-linking the raw
    RSS image_url straight from the browser silently fails and every
    article falls back to the same placeholder. Pulling the image through
    our own server (with a normal browser User-Agent and no cross-site
    Referer problem) and caching it to disk fixes that and also means
    repeat page views don't re-hit the publisher at all.
    """
    src = request.args.get("u", "").strip()
    if not src or not src.lower().startswith(("http://", "https://")):
        abort(400)

    key = hashlib.sha1(src.encode()).hexdigest()
    ext = os.path.splitext(urlparse(src).path)[1].lower()
    if ext not in (".jpg", ".jpeg", ".png", ".webp", ".gif", ".avif"):
        ext = ".jpg"
    cache_dir = os.path.join(current_app.root_path, _IMG_CACHE_DIR)
    os.makedirs(cache_dir, exist_ok=True)
    cache_path = os.path.join(cache_dir, key + ext)

    if os.path.exists(cache_path):
        return send_file(cache_path, max_age=86400)

    try:
        resp = requests.get(src, headers=_IMG_HEADERS, timeout=6, stream=True)
        if resp.status_code != 200 or not resp.headers.get("Content-Type", "").startswith("image"):
            raise ValueError(f"bad response {resp.status_code}")
        data = resp.content
        with open(cache_path, "wb") as f:
            f.write(data)
        return Response(data, mimetype=resp.headers.get("Content-Type", "image/jpeg"),
                         headers={"Cache-Control": "public, max-age=86400"})
    except Exception as ex:
        log.info("image_proxy fetch failed for %s: %s", src, ex)
        abort(404)


@public_bp.route("/api/pulse")
def pulse():
    """Ultra-cheap 'has anything changed' check for the live-update script.

    The frontend polls this every second. It's a single indexed aggregate
    query against our own database (no external feed calls happen here at
    all), so polling it every second is safe and fast. When the returned
    version string changes, the JS fetches the actual page HTML and swaps
    the live region in place -- that's what makes channels/feeds/photos
    update within about a second of new content landing, without re-hitting
    any publisher's RSS URL more often than the scheduler already does.
    """
    latest = db.session.query(func.max(Article.id), func.count(Article.id)).first()
    max_id = latest[0] or 0
    count = latest[1] or 0
    return {"v": f"{max_id}-{count}"}


def _paginate(query, per_page=12):
    page = int(request.args.get("page", 1))
    return query.paginate(page=page, per_page=per_page, error_out=False)


from flask_login import current_user, login_required

@public_bp.route("/")
@cache.cached(timeout=120, key_prefix="home", query_string=True,
              unless=lambda: current_user.is_authenticated)
def home():
    hero     = Article.query.order_by(desc(Article.published_at)).limit(1).first()
    breaking = Article.query.filter_by(is_breaking=True).order_by(desc(Article.published_at)).limit(8).all()
    top      = Article.query.order_by(desc(Article.published_at)).limit(8).all()
    trending = Article.query.filter_by(is_trending=True).order_by(desc(Article.published_at)).limit(6).all()
    # Paginated so every fetched article is reachable straight from the
    # home feed instead of being capped at a fixed handful.
    latest   = _paginate(Article.query.order_by(desc(Article.published_at)), per_page=18)

    sections = {}
    for cat in Category.query.order_by(Category.sort_order).limit(6).all():
        sections[cat.slug] = {
            "category": cat,
            "articles": Article.query.filter_by(category_id=cat.id)
                                     .order_by(desc(Article.published_at))
                                     .limit(4).all()
        }

    # One row per active channel/source — every source shows up, even ones
    # that haven't been fetched yet (those get a placeholder). Each channel
    # carries a short list of its latest headlines (title + time only —
    # no photo card, so nothing overlaps/overwrites another element) that
    # links through to a full per-channel page showing every article from
    # that source, newest first.
    by_channel = []
    for src in Source.query.filter_by(is_active=True).order_by(Source.name).all():
        headlines = (Article.query.filter_by(source_id=src.id)
                     .order_by(desc(Article.published_at)).limit(6).all())
        by_channel.append((src, headlines))
    by_channel.sort(
        key=lambda row: row[1][0].published_at if row[1] and row[1][0].published_at else datetime.min,
        reverse=True
    )

    return render_template("news/home.html",
                           hero=hero, breaking=breaking, top=top,
                           trending=trending, latest=latest, sections=sections,
                           by_channel=by_channel)


@public_bp.route("/breaking")
def breaking():
    items = Article.query.filter_by(is_breaking=True).order_by(desc(Article.published_at)).paginate(per_page=15)
    return render_template("news/breaking.html", items=items)


@public_bp.route("/top-stories")
def top_stories():
    items = Article.query.order_by(desc(Article.published_at), desc(Article.views)).limit(20).all()
    return render_template("news/top_stories.html", items=items)


@public_bp.route("/trending")
def trending():
    items = Article.query.filter_by(is_trending=True).order_by(desc(Article.published_at)).paginate(per_page=15)
    return render_template("news/trending.html", items=items)


@public_bp.route("/yesterday")
def yesterday():
    """Everything published on the previous calendar day -- a dedicated
    tab next to Breaking/Trending/Top Stories so older news (which is
    never deleted, just outranked by newer stories everywhere else) stays
    easy to find instead of scrolling through pagination to get to it."""
    today_start = datetime.combine(datetime.utcnow().date(), datetime.min.time())
    yesterday_start = today_start - timedelta(days=1)
    page = int(request.args.get("page", 1))
    items = (Article.query
             .filter(Article.published_at >= yesterday_start, Article.published_at < today_start)
             .order_by(desc(Article.published_at))
             .paginate(page=page, per_page=20, error_out=False))
    return render_template("news/yesterday.html", items=items, day=yesterday_start)


@public_bp.route("/category/<slug>")
def category(slug):
    cat = Category.query.filter_by(slug=slug).first_or_404()
    page = int(request.args.get("page", 1))
    items = Article.query.filter_by(category_id=cat.id).order_by(desc(Article.published_at)) \
                          .paginate(page=page, per_page=12, error_out=False)

    # Category-wise "other platforms" breakdown -- every active source that
    # has published at least one article in *this* category gets its own
    # box with its latest headlines from this category only, same as the
    # homepage's all-category channel view but scoped down. This lives
    # inside #live-region so it auto-refreshes the moment a source's feed
    # changes (new/updated articles), with no page reload needed.
    by_channel = []
    sources_in_cat = (Source.query.join(Article, Article.source_id == Source.id)
                       .filter(Source.is_active == True, Article.category_id == cat.id)
                       .distinct().all())
    for src in sources_in_cat:
        headlines = (Article.query.filter_by(source_id=src.id, category_id=cat.id)
                     .order_by(desc(Article.published_at)).limit(6).all())
        if headlines:
            by_channel.append((src, headlines))
    by_channel.sort(key=lambda row: row[1][0].published_at or datetime.min, reverse=True)

    return render_template("news/category.html", cat=cat, items=items, by_channel=by_channel)


@public_bp.route("/channel/<int:source_id>")
def channel(source_id):
    src = Source.query.get_or_404(source_id)
    page = int(request.args.get("page", 1))
    items = (Article.query.filter_by(source_id=src.id)
             .order_by(desc(Article.published_at))
             .paginate(page=page, per_page=20, error_out=False))

    # Group this page's articles by calendar date so the template can show
    # a date header, with each story underneath it stamped with its time —
    # i.e. "all news, date wise and time".
    grouped = []
    current_day, bucket = None, None
    for a in items.items:
        day = a.published_at.date() if a.published_at else None
        if day != current_day:
            bucket = {"date": a.published_at, "articles": []}
            grouped.append(bucket)
            current_day = day
        bucket["articles"].append(a)

    return render_template("news/channel.html", src=src, items=items, grouped=grouped)


@public_bp.route("/search")
def search():
    q = request.args.get("q", "").strip()
    sort = request.args.get("sort", "newest")
    page = int(request.args.get("page", 1))
    query = Article.query
    if q:
        query = query.filter(or_(Article.title.ilike(f"%{q}%"),
                                 Article.summary.ilike(f"%{q}%")))
    if sort == "oldest":
        query = query.order_by(Article.published_at)
    elif sort == "popular":
        query = query.order_by(desc(Article.views))
    else:
        query = query.order_by(desc(Article.published_at))
    results = query.paginate(page=page, per_page=12, error_out=False)
    return render_template("news/search.html", results=results, q=q, sort=sort)


@public_bp.route("/about")
def about():
    return render_template("news/about.html")


@public_bp.route("/contact")
def contact():
    return render_template("news/contact.html")


@public_bp.route("/privacy")
def privacy():
    return render_template("news/privacy.html")


@public_bp.route("/terms")
def terms():
    return render_template("news/terms.html")


# ---- stub-but-render pages so navigation never 404s ----
@public_bp.route("/local")
def local_news():
    page = int(request.args.get("page", 1))
    items = Article.query.order_by(desc(Article.published_at)) \
                          .paginate(page=page, per_page=20, error_out=False)
    return render_template("news/category.html",
                           cat=Category(slug="local", name="Local", icon="bi-geo-alt", color="#22c55e"),
                           items=items)
