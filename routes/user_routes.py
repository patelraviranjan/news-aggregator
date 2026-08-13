"""User account: profile, settings, bookmarks, reading history, notifications."""
import os
from flask import Blueprint, render_template, request, redirect, url_for, flash, current_app
from flask_login import login_required, current_user
from werkzeug.utils import secure_filename
from app_modules.extensions import db
from app_modules.models import (Article, Bookmark, ReadingHistory,
                                 Notification, Like)
from app_modules.security import csrf_required

user_bp = Blueprint("user", __name__)


def _save_avatar(file_storage):
    if not file_storage or not file_storage.filename:
        return None
    fname = secure_filename(f"{current_user.id}_{file_storage.filename}")
    upload_dir = os.path.join(current_app.config["UPLOAD_FOLDER"])
    os.makedirs(upload_dir, exist_ok=True)
    file_storage.save(os.path.join(upload_dir, fname))
    return f"uploads/{fname}"


@user_bp.route("/profile", methods=["GET", "POST"])
@login_required
@csrf_required
def profile():
    from app_modules.models import Admin
    if isinstance(current_user, Admin):
        flash("Admin accounts don't have a public profile. Use the admin dashboard instead.", "info")
        return redirect(url_for("admin.dashboard"))
    if request.method == "POST":
        country = (request.form.get("country") or "").strip()
        phone = (request.form.get("phone") or "").strip()
        address = (request.form.get("address") or "").strip()
        avatar_file = request.files.get("avatar")
        has_avatar_already = bool(current_user.avatar) and current_user.avatar != "images/default-avatar.png"

        if not country or not phone or not address:
            flash("Address, country and phone number are compulsory.", "danger")
            return redirect(url_for("user.profile"))
        if not has_avatar_already and (not avatar_file or not avatar_file.filename):
            flash("A profile image is compulsory. Please upload one.", "danger")
            return redirect(url_for("user.profile"))

        current_user.full_name = request.form.get("full_name", current_user.full_name)
        current_user.bio = request.form.get("bio")
        current_user.country = country
        current_user.phone = phone
        current_user.address = address
        fave = request.form.getlist("favorite_categories")
        current_user.favorite_categories = ",".join(fave)
        avatar_path = _save_avatar(avatar_file)
        if avatar_path:
            current_user.avatar = avatar_path
        db.session.commit()
        flash("Profile updated.", "success")
        return redirect(url_for("user.profile"))
    from app_modules.models import Category
    cats = Category.query.all()
    return render_template("user/profile.html", categories=cats)


@user_bp.route("/settings", methods=["GET", "POST"])
@login_required
@csrf_required
def settings():
    if request.method == "POST":
        current_user.language = request.form.get("language", "en")
        current_user.country  = request.form.get("country", "")
        current_user.push_enabled = bool(request.form.get("push_enabled"))
        if request.form.get("new_password"):
            if request.form.get("new_password") == request.form.get("confirm_password"):
                current_user.set_password(request.form.get("new_password"))
                flash("Password changed.", "success")
            else:
                flash("Passwords don't match.", "danger")
                return redirect(url_for("user.settings"))
        db.session.commit()
        flash("Settings saved.", "success")
        return redirect(url_for("user.settings"))
    return render_template("user/settings.html")


@user_bp.route("/bookmarks")
@login_required
def bookmarks():
    items = (db.session.query(Article, Bookmark)
                .join(Bookmark, Bookmark.article_id == Article.id)
                .filter(Bookmark.user_id == current_user.id)
                .order_by(Bookmark.created_at.desc()).all())
    return render_template("user/bookmarks.html", pairs=items)


@user_bp.route("/bookmark/<int:article_id>", methods=["POST"])
@login_required
@csrf_required
def toggle_bookmark(article_id):
    a = Article.query.get_or_404(article_id)
    existing = Bookmark.query.filter_by(user_id=current_user.id, article_id=a.id).first()
    if existing:
        db.session.delete(existing); flash("Removed bookmark.", "info")
    else:
        db.session.add(Bookmark(user_id=current_user.id, article_id=a.id))
        flash("Bookmarked.", "success")
    db.session.commit()
    return redirect(request.referrer or url_for("public.home"))


@user_bp.route("/history")
@login_required
def history():
    items = (db.session.query(Article, ReadingHistory)
                .join(ReadingHistory, ReadingHistory.article_id == Article.id)
                .filter(ReadingHistory.user_id == current_user.id)
                .order_by(ReadingHistory.read_at.desc()).limit(50).all())
    return render_template("user/history.html", pairs=items)


@user_bp.route("/notifications")
@login_required
def notifications():
    items = Notification.query.filter_by(user_id=current_user.id) \
                              .order_by(Notification.created_at.desc()).limit(50).all()
    for n in items:
        if not n.is_read:
            n.is_read = True
    db.session.commit()
    return render_template("user/notifications.html", notifications=items)
