"""Admin panel: dashboard, manage users/articles/categories/sources."""
import logging
import os
import re
from datetime import datetime
from sqlalchemy import func
from werkzeug.utils import secure_filename
from flask import Blueprint, render_template, request, redirect, url_for, flash, jsonify, current_app
from flask_login import login_required, current_user
from app_modules.extensions import db
from app_modules.models import (User, Article, Category, Source,
                                 Admin, ActivityLog, Report,
                                 Bookmark, ReadingHistory, Comment)
from app_modules.security import admin_required, csrf_required, sanitize_html
from services.news_fetcher import discover_feeds, fetch_rss, persist, resolve_new_source

log = logging.getLogger(__name__)



def _save_admin_avatar(file_storage, owner_id):
    """Save an uploaded admin profile image and return its relative static path, or None."""
    if not file_storage or not file_storage.filename:
        return None
    fname = secure_filename(f"admin{owner_id}_{file_storage.filename}")
    upload_dir = current_app.config["UPLOAD_FOLDER"]
    os.makedirs(upload_dir, exist_ok=True)
    file_storage.save(os.path.join(upload_dir, fname))
    return f"uploads/{fname}"


def _slugify(text: str) -> str:
    text = (text or "").lower()[:200]
    return re.sub(r"[^a-z0-9]+", "-", text).strip("-") or "article"


def _unique_slug(base_slug, article_id=None):
    slug = base_slug
    n = 2
    q = Article.query.filter_by(slug=slug)
    if article_id:
        q = q.filter(Article.id != article_id)
    while q.first() is not None:
        slug = f"{base_slug}-{n}"
        n += 1
        q = Article.query.filter_by(slug=slug)
        if article_id:
            q = q.filter(Article.id != article_id)
    return slug


def _parse_datetime(value):
    if not value:
        return None
    for fmt in ("%Y-%m-%dT%H:%M", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
        try:
            return datetime.strptime(value, fmt)
        except ValueError:
            continue
    return None

admin_bp = Blueprint("admin", __name__)


@admin_bp.before_request
@login_required
def guard():
    if not (current_user.is_authenticated and isinstance(current_user, Admin)):
        return redirect(url_for("auth.admin_login"))


@admin_bp.route("/")
def dashboard():
    stats = {
        "users":     User.query.count(),
        "articles":  Article.query.count(),
        "categories": Category.query.count(),
        "sources":   Source.query.count(),
        "admins":    Admin.query.count(),
        "views":     db.session.query(func.coalesce(func.sum(Article.views), 0)).scalar(),
    }
    recent_articles = Article.query.order_by(Article.id.desc()).limit(10).all()
    recent_users    = User.query.order_by(User.id.desc()).limit(10).all()
    cat_counts      = [list(row) for row in db.session.query(Category.name, func.count(Article.id))
                                  .join(Article, Article.category_id == Category.id)
                                  .group_by(Category.name).all()]
    return render_template("admin/dashboard.html", stats=stats,
                           recent_articles=recent_articles, recent_users=recent_users,
                           cat_counts=cat_counts)


@admin_bp.route("/articles")
def articles():
    page = int(request.args.get("page", 1))
    date_str = (request.args.get("date") or "").strip()
    query = Article.query

    selected_date = None
    if date_str:
        try:
            selected_date = datetime.strptime(date_str, "%Y-%m-%d").date()
        except ValueError:
            selected_date = None
        if selected_date:
            # Filter to that calendar day (published_at, not fetched_at --
            # editors care about the article's news date, not when the
            # scraper happened to grab it).
            start = datetime.combine(selected_date, datetime.min.time())
            end = datetime.combine(selected_date, datetime.max.time())
            query = query.filter(Article.published_at >= start, Article.published_at <= end)

    items = query.order_by(Article.published_at.desc(), Article.id.desc()) \
                  .paginate(page=page, per_page=20, error_out=False)

    # Distinct calendar dates that actually have articles, newest first --
    # powers the date picker's quick-jump list so admins can see which days
    # have coverage without guessing.
    available_dates = (db.session.query(func.date(Article.published_at))
                        .distinct()
                        .order_by(func.date(Article.published_at).desc())
                        .limit(60).all())
    available_dates = [d[0] for d in available_dates if d[0]]

    return render_template("admin/articles.html", items=items,
                            selected_date=date_str, available_dates=available_dates)


def _article_form_context(article=None):
    return {
        "article": article,
        "categories": Category.query.order_by(Category.sort_order).all(),
        "sources": Source.query.order_by(Source.name).all(),
    }


@admin_bp.route("/articles/new", methods=["GET", "POST"])
@csrf_required
def new_article():
    if request.method == "POST":
        title = request.form.get("title", "").strip()
        if not title:
            flash("Title is required.", "warning")
            return render_template("admin/article_form.html", **_article_form_context())

        raw_slug = request.form.get("slug", "").strip() or title
        slug = _unique_slug(_slugify(raw_slug))

        source = Source.query.get(request.form.get("source_id") or 0)
        article = Article(
            title=title,
            slug=slug,
            summary=request.form.get("summary", "").strip(),
            content=sanitize_html(request.form.get("content", "")),
            image_url=request.form.get("image_url", "").strip(),
            source_id=source.id if source else None,
            source_name=source.name if source else request.form.get("source_name", "").strip(),
            category_id=request.form.get("category_id") or None,
            author=request.form.get("author", "").strip(),
            url=request.form.get("url", "").strip(),
            published_at=_parse_datetime(request.form.get("published_at")) or datetime.utcnow(),
            language=request.form.get("language", "en"),
            country=request.form.get("country", "global"),
            is_breaking=bool(request.form.get("is_breaking")),
            is_trending=bool(request.form.get("is_trending")),
            reading_time=int(request.form.get("reading_time") or 3),
        )
        db.session.add(article)
        db.session.commit()
        flash("Article created.", "success")
        return redirect(url_for("admin.articles"))

    return render_template("admin/article_form.html", **_article_form_context())


@admin_bp.route("/articles/<int:aid>/edit", methods=["GET", "POST"])
@csrf_required
def edit_article(aid):
    article = Article.query.get_or_404(aid)

    if request.method == "POST":
        title = request.form.get("title", "").strip()
        if not title:
            flash("Title is required.", "warning")
            return render_template("admin/article_form.html", **_article_form_context(article))

        raw_slug = request.form.get("slug", "").strip() or title
        article.slug = _unique_slug(_slugify(raw_slug), article_id=article.id)

        source = Source.query.get(request.form.get("source_id") or 0)
        article.title = title
        article.summary = request.form.get("summary", "").strip()
        article.content = sanitize_html(request.form.get("content", ""))
        article.image_url = request.form.get("image_url", "").strip()
        article.source_id = source.id if source else None
        article.source_name = source.name if source else request.form.get("source_name", "").strip()
        article.category_id = request.form.get("category_id") or None
        article.author = request.form.get("author", "").strip()
        article.url = request.form.get("url", "").strip()
        published_at = _parse_datetime(request.form.get("published_at"))
        if published_at:
            article.published_at = published_at
        article.language = request.form.get("language", "en")
        article.country = request.form.get("country", "global")
        article.is_breaking = bool(request.form.get("is_breaking"))
        article.is_trending = bool(request.form.get("is_trending"))
        article.reading_time = int(request.form.get("reading_time") or 3)

        db.session.commit()
        flash("Article updated.", "success")
        return redirect(url_for("admin.articles"))

    return render_template("admin/article_form.html", **_article_form_context(article))


@admin_bp.route("/articles/<int:aid>/delete", methods=["POST"])
@csrf_required
def delete_article(aid):
    a = Article.query.get_or_404(aid); db.session.delete(a); db.session.commit()
    flash("Article deleted.", "info"); return redirect(url_for("admin.articles"))


@admin_bp.route("/admins", methods=["GET", "POST"])
@csrf_required
def admins():
    """List admin/staff accounts and let a superadmin add new ones."""
    if current_user.role != "superadmin":
        flash("Only a superadmin can manage admin accounts.", "warning")
        return redirect(url_for("admin.dashboard"))

    if request.method == "POST":
        name = request.form.get("name", "").strip()
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")
        role = request.form.get("role", "editor").strip() or "editor"

        if not name or not email or not password:
            flash("Name, email and password are required.", "warning")
            return redirect(url_for("admin.admins"))
        if Admin.query.filter_by(email=email).first():
            flash(f"An admin with email \"{email}\" already exists.", "warning")
            return redirect(url_for("admin.admins"))
        if role not in ("superadmin", "editor", "moderator"):
            role = "editor"

        a = Admin(name=name, email=email, role=role,
                  phone=request.form.get("phone", "").strip(),
                  country=request.form.get("country", "").strip(),
                  address=request.form.get("address", "").strip())
        a.set_password(password)
        db.session.add(a)
        db.session.flush()  # assign a.id so the avatar filename can use it
        avatar_path = _save_admin_avatar(request.files.get("avatar"), a.id)
        if avatar_path:
            a.avatar = avatar_path
        db.session.commit()
        flash(f"Admin \"{name}\" added.", "success")
        return redirect(url_for("admin.admins"))

    items = Admin.query.order_by(Admin.id.desc()).all()
    return render_template("admin/admins.html", items=items)


@admin_bp.route("/admins/<int:aid>")
def admin_detail(aid):
    """Full profile view of one admin — name, email, phone, address, country, photo."""
    if current_user.role != "superadmin" and current_user.id != aid:
        flash("Only a superadmin can view other admins' details.", "warning")
        return redirect(url_for("admin.dashboard"))
    a = Admin.query.get_or_404(aid)
    return render_template("admin/admin_detail.html", admin_user=a)


@admin_bp.route("/admins/<int:aid>/edit", methods=["POST"])
@csrf_required
def edit_admin(aid):
    if current_user.role != "superadmin":
        flash("Only a superadmin can manage admin accounts.", "warning")
        return redirect(url_for("admin.dashboard"))

    a = Admin.query.get_or_404(aid)
    name = request.form.get("name", "").strip()
    email = request.form.get("email", "").strip().lower()
    role = request.form.get("role", a.role).strip() or a.role
    if not name or not email:
        flash("Name and email are required.", "warning")
        return redirect(url_for("admin.admins"))

    dup = Admin.query.filter(Admin.email == email, Admin.id != a.id).first()
    if dup:
        flash(f"That email is already used by \"{dup.name}\".", "warning")
        return redirect(url_for("admin.admins"))

    if a.role == "superadmin" and role != "superadmin" and \
            Admin.query.filter_by(role="superadmin").count() <= 1:
        flash("Can't demote the last remaining superadmin.", "warning")
        return redirect(url_for("admin.admins"))

    a.name = name
    a.email = email
    a.role = role if role in ("superadmin", "editor", "moderator") else a.role
    a.phone = request.form.get("phone", "").strip()
    a.country = request.form.get("country", "").strip()
    a.address = request.form.get("address", "").strip()
    avatar_path = _save_admin_avatar(request.files.get("avatar"), a.id)
    if avatar_path:
        a.avatar = avatar_path
    new_password = request.form.get("password", "")
    if new_password:
        a.set_password(new_password)
    db.session.commit()
    flash(f"\"{a.name}\" updated.", "success")
    return redirect(url_for("admin.admins"))


@admin_bp.route("/admins/<int:aid>/delete", methods=["POST"])
@csrf_required
def delete_admin(aid):
    if current_user.role != "superadmin":
        flash("Only a superadmin can manage admin accounts.", "warning")
        return redirect(url_for("admin.dashboard"))

    a = Admin.query.get_or_404(aid)
    if a.id == current_user.id:
        flash("You can't delete your own account while logged in.", "warning")
        return redirect(url_for("admin.admins"))
    if a.role == "superadmin" and Admin.query.filter_by(role="superadmin").count() <= 1:
        flash("Can't delete the last remaining superadmin.", "warning")
        return redirect(url_for("admin.admins"))

    name = a.name
    db.session.delete(a)
    db.session.commit()
    flash(f"Admin \"{name}\" deleted.", "success")
    return redirect(url_for("admin.admins"))


@admin_bp.route("/profile", methods=["GET", "POST"])
@csrf_required
def profile():
    if request.method == "POST":
        name = request.form.get("name", "").strip()
        address = (request.form.get("address") or "").strip()
        country = (request.form.get("country") or "").strip()
        phone = (request.form.get("phone") or "").strip()
        avatar_file = request.files.get("avatar")
        has_avatar_already = bool(current_user.avatar) and current_user.avatar != "images/default-avatar.png"

        if not address or not country or not phone:
            flash("Address, country and phone number are compulsory.", "danger")
            return redirect(url_for("admin.profile"))
        if not has_avatar_already and (not avatar_file or not avatar_file.filename):
            flash("A profile image is compulsory. Please upload one.", "danger")
            return redirect(url_for("admin.profile"))

        if name:
            current_user.name = name
        current_user.address = address
        current_user.country = country
        current_user.phone = phone
        avatar_path = _save_admin_avatar(avatar_file, current_user.id)
        if avatar_path:
            current_user.avatar = avatar_path
        new_password = request.form.get("new_password")
        if new_password:
            if new_password == request.form.get("confirm_password"):
                current_user.set_password(new_password)
                flash("Password changed.", "success")
            else:
                flash("Passwords don't match.", "danger")
                return redirect(url_for("admin.profile"))
        db.session.commit()
        flash("Profile updated.", "success")
        return redirect(url_for("admin.profile"))
    return render_template("admin/profile.html")


@admin_bp.route("/users")
def users():
    items = User.query.order_by(User.id.desc()).all()
    return render_template("admin/users.html", users=items)


@admin_bp.route("/users/<int:uid>")
def user_detail(uid):
    user = User.query.get_or_404(uid)
    bookmarks_count = Bookmark.query.filter_by(user_id=user.id).count()
    history_count = ReadingHistory.query.filter_by(user_id=user.id).count()
    comments_count = Comment.query.filter_by(user_id=user.id).count()
    recent_history = (db.session.query(Article, ReadingHistory)
                         .join(ReadingHistory, ReadingHistory.article_id == Article.id)
                         .filter(ReadingHistory.user_id == user.id)
                         .order_by(ReadingHistory.read_at.desc()).limit(10).all())
    return render_template("admin/user_detail.html", user=user,
                           bookmarks_count=bookmarks_count,
                           history_count=history_count,
                           comments_count=comments_count,
                           recent_history=recent_history)


@admin_bp.route("/categories", methods=["GET", "POST"])
@csrf_required
def categories():
    if request.method == "POST":
        cat = Category(
            slug=request.form["slug"], name=request.form["name"],
            icon=request.form.get("icon", "bi-newspaper"),
            color=request.form.get("color", "#2563eb"),
            sort_order=int(request.form.get("sort_order", 99)),
            description=request.form.get("description", "")
        )
        db.session.add(cat); db.session.commit()
        flash("Category created.", "success")
        return redirect(url_for("admin.categories"))
    items = Category.query.order_by(Category.sort_order).all()
    uncategorized_count = Article.query.filter(Article.category_id.is_(None)).count()
    return render_template("admin/categories.html", items=items,
                            uncategorized_count=uncategorized_count)


@admin_bp.route("/categories/backfill", methods=["POST"])
@csrf_required
def categories_backfill():
    """One-time cleanup for articles fetched before auto-categorisation
    existed (category_id is NULL) -- runs the same keyword classifier
    used for new fetches against every existing uncategorized row.
    """
    from services.news_fetcher import guess_category

    all_categories = {c.slug: c for c in Category.query.all()}
    updated = 0
    stale = Article.query.filter(Article.category_id.is_(None)).all()
    for a in stale:
        slug = guess_category(a.title, a.summary)
        cat = all_categories.get(slug)
        if cat:
            a.category_id = cat.id
            updated += 1
    db.session.commit()
    if updated:
        try:
            from app_modules.extensions import cache
            cache.clear()
        except Exception:
            pass
    flash(f"Categorized {updated} previously-uncategorized article(s).", "success")
    return redirect(url_for("admin.categories"))


@admin_bp.route("/sources", methods=["GET", "POST"])
@admin_bp.route("/feed", methods=["GET", "POST"], endpoint="feed")
@csrf_required
def sources():
    if request.method == "POST":
        name = request.form.get("name", "").strip()
        rss_url = request.form.get("rss_url", "").strip()
        url = request.form.get("url", "").strip()
        if not rss_url:
            flash("An RSS feed URL is required. Paste it below (or use \"Find feed\" "
                  "if you only have the website's homepage link) and we'll fetch "
                  "the site's name automatically.", "warning")
            return redirect(url_for("admin.sources"))

        existing = Source.query.filter_by(rss_url=rss_url).first()
        if existing:
            flash(f"That feed is already added as \"{existing.name}\".", "warning")
            return redirect(url_for("admin.sources"))

        # Name (and website URL, if not typed in) are auto-detected straight
        # from the RSS feed itself -- so pasting just the feed URL is enough
        # to add a source, no separate lookup step required.
        resolved = resolve_new_source(name, url, rss_url)
        if resolved["preview"] is None:
            flash("Couldn't read that as an RSS feed -- double check the URL, "
                  "or use \"Find feed\" above to search a website for its feed.", "danger")
            return redirect(url_for("admin.sources"))

        s = Source(name=resolved["name"], url=resolved["url"],
                   rss_url=rss_url,
                   country=request.form.get("country", "global").strip() or "global",
                   reliability=int(request.form.get("reliability", 80) or 80))
        db.session.add(s)
        db.session.commit()

        inserted = 0
        if request.form.get("fetch_now") == "1":
            source_name = s.name
            try:
                items = list(fetch_rss(s.rss_url, s.name, limit=None))
                inserted = persist(current_app._get_current_object(), items,
                                    source_model=s, max_image_enrich=min(len(items), 25))
            except Exception as ex:
                db.session.rollback()
                flash(f"Source added, but the first fetch failed: {ex}", "warning")
                return redirect(url_for("admin.sources"))

        if inserted:
            flash(f"\"{s.name}\" added \u2014 pulled {inserted} article(s) right away.", "success")
        else:
            flash(f"\"{s.name}\" added.", "success")
        return redirect(url_for("admin.sources"))
    items = Source.query.order_by(Source.name).all()
    return render_template("admin/sources.html", items=items)


@admin_bp.route("/sources/discover", methods=["POST"])
@csrf_required
def discover_source():
    """AJAX: given ANY website URL (BBC, Aaj Tak, OnlineKhabar, Hindustan, Al
    Jazeera, Kantipur, Gorkha News, GNews, etc.), find its working RSS feed(s)
    so the admin doesn't have to hunt for the raw .xml link themselves."""
    website_url = (request.form.get("website_url") or "").strip()
    if not website_url:
        return jsonify({"ok": False, "error": "Enter a website URL first."}), 400
    try:
        result = discover_feeds(website_url)
    except Exception as ex:
        return jsonify({"ok": False, "error": str(ex)}), 500
    if not result["candidates"]:
        return jsonify({
            "ok": False,
            "error": "Couldn't find a working RSS feed on that site. "
                     "Try pasting the direct feed URL (often ending in /feed or .xml) instead."
        }), 404
    return jsonify({"ok": True, "site_url": result["site_url"], "candidates": result["candidates"]})


@admin_bp.route("/sources/<int:sid>/articles", methods=["GET"])
def source_articles(sid):
    """AJAX: latest articles already pulled from this source, so the admin
    can see & read what a feed brought in without leaving this page."""
    s = Source.query.get_or_404(sid)
    rows = (Article.query.filter_by(source_id=s.id)
            .order_by(Article.published_at.desc())
            .limit(15).all())
    return jsonify({
        "ok": True,
        "source": s.name,
        "count": len(rows),
        "articles": [{
            "title": a.title,
            "summary": (a.summary or "")[:180],
            "published_at": a.published_at.strftime("%d %b %Y, %H:%M") if a.published_at else "",
            "read_url": url_for("news.article_detail", slug=a.slug),
            "original_url": a.url,
            "image_url": a.image_url,
        } for a in rows],
    })


@admin_bp.route("/sources/<int:sid>/edit", methods=["POST"])
@csrf_required
def edit_source(sid):
    """Update an existing source's details (name, URLs, country, reliability)."""
    s = Source.query.get_or_404(sid)
    name = request.form.get("name", "").strip()
    rss_url = request.form.get("rss_url", "").strip()
    if not name or not rss_url:
        flash("Name and RSS feed URL are required.", "warning")
        return redirect(url_for("admin.sources"))

    dup = Source.query.filter(Source.rss_url == rss_url, Source.id != s.id).first()
    if dup:
        flash(f"That feed URL is already used by \"{dup.name}\".", "warning")
        return redirect(url_for("admin.sources"))

    rss_url_changed = rss_url != s.rss_url

    s.name = name
    s.url = request.form.get("url", "").strip()
    s.rss_url = rss_url
    s.country = request.form.get("country", "global").strip() or "global"
    try:
        s.reliability = int(request.form.get("reliability", 80) or 80)
    except ValueError:
        s.reliability = 80
    s.is_active = request.form.get("is_active") == "1"
    db.session.commit()

    # Pull fresh articles immediately whenever the feed URL itself changed
    # (or it's explicitly requested), so editing a source updates the site
    # right away instead of waiting for the next scheduled fetch cycle.
    inserted = 0
    if s.is_active and (rss_url_changed or request.form.get("fetch_now") == "1"):
        try:
            items = list(fetch_rss(s.rss_url, s.name, limit=None))
            inserted = persist(current_app._get_current_object(), items,
                                source_model=s, max_image_enrich=min(len(items), 25))
        except Exception as ex:
            flash(f"\"{s.name}\" updated, but the refresh fetch failed: {ex}", "warning")
            return redirect(url_for("admin.sources"))

    if inserted:
        flash(f"\"{s.name}\" updated — pulled {inserted} article(s) right away.", "success")
    else:
        flash(f"\"{s.name}\" updated.", "success")
    return redirect(url_for("admin.sources"))


@admin_bp.route("/sources/<int:sid>/delete", methods=["POST"])
@csrf_required
def delete_source(sid):
    """Remove a source. Articles already pulled from it are kept (source_name
    stays denormalised on them) but are detached from the deleted Source row."""
    s = Source.query.get_or_404(sid)
    name = s.name
    Article.query.filter_by(source_id=s.id).update({"source_id": None})
    db.session.delete(s)
    db.session.commit()
    flash(f"\"{name}\" deleted.", "success")
    return redirect(url_for("admin.sources"))


@admin_bp.route("/sources/<int:sid>/fetch", methods=["POST"])
@csrf_required
def fetch_source_now(sid):
    """Manually pull the latest articles for one source right now,
    instead of waiting for the scheduled background job."""
    s = Source.query.get_or_404(sid)
    source_name = s.name
    if not s.rss_url:
        flash(f"{source_name} has no RSS feed URL set.", "warning")
        return redirect(url_for("admin.sources"))
    try:
        items = list(fetch_rss(s.rss_url, s.name, limit=None))
        inserted = persist(current_app._get_current_object(), items,
                            source_model=s, max_image_enrich=min(len(items), 25))
        flash(f"{source_name}: pulled {inserted} new article(s).", "success")
    except Exception as ex:
        db.session.rollback()
        flash(f"Fetch failed for {source_name}: {ex}", "danger")
    return redirect(url_for("admin.sources"))


@admin_bp.route("/sources/fetch-all", methods=["POST"])
@csrf_required
def fetch_all_sources():
    """Manually pull today's news from every active source right now,
    instead of clicking 'Fetch now' on each one or waiting for the next
    scheduled cycle. Same per-source fetch+persist the scheduler runs,
    just triggered on demand across the whole list in one click."""
    sources = Source.query.filter_by(is_active=True).all()
    if not sources:
        flash("No active sources to fetch from yet — add one below first.", "warning")
        return redirect(url_for("admin.sources"))

    app_obj = current_app._get_current_object()
    total_inserted = 0
    failed = []
    for s in sources:
        if not s.rss_url:
            continue
        try:
            items = list(fetch_rss(s.rss_url, s.name, limit=None))
            total_inserted += persist(app_obj, items, source_model=s,
                                       max_image_enrich=min(len(items), 25))
        except Exception as ex:
            db.session.rollback()
            failed.append(s.name)
            log.warning("fetch_all_sources: %s failed: %s", s.name, ex)

    if total_inserted:
        flash(f"Fetched today's news from {len(sources)} source(s) \u2014 "
              f"{total_inserted} new article(s) pulled in.", "success")
    else:
        flash("Fetch complete \u2014 no new articles (everything's already up to date).", "info")
    if failed:
        flash(f"Couldn't reach: {', '.join(failed)}.", "warning")
    return redirect(url_for("admin.sources"))


@admin_bp.route("/logs")
def logs():
    items = ActivityLog.query.order_by(ActivityLog.id.desc()).limit(200).all()
    return render_template("admin/logs.html", items=items)
