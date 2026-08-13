"""News routes: article detail + comments."""
from flask import Blueprint, render_template, request, redirect, url_for, flash, abort
from flask_login import login_required, current_user
from sqlalchemy import desc, or_
from app_modules.extensions import db
from app_modules.models import Article, Comment, Like, ReadingHistory
from app_modules.security import sanitize_html, csrf_required

news_bp = Blueprint("news", __name__)


@news_bp.route("/<slug>")
def article_detail(slug):
    a = Article.query.filter_by(slug=slug).first_or_404()
    a.views = (a.views or 0) + 1
    if current_user.is_authenticated:
        db.session.add(ReadingHistory(user_id=current_user.id, article_id=a.id, progress=100))
    db.session.commit()

    # Guests get roughly the first half of the article body; logged-in
    # readers get the whole thing. Split on whole words so it doesn't cut
    # a word in half.
    full_body = a.content or a.summary or ""
    if current_user.is_authenticated:
        preview_body = full_body
    else:
        words = full_body.split()
        preview_body = " ".join(words[: max(1, len(words) // 2)])

    related = []
    if a.category_id:
        related = Article.query.filter(Article.category_id == a.category_id,
                                       Article.id != a.id) \
                                 .order_by(desc(Article.published_at)).limit(6).all()
    prev_a = Article.query.filter(Article.id < a.id).order_by(desc(Article.id)).first()
    next_a = Article.query.filter(Article.id > a.id).order_by(Article.id).first()
    comments = Comment.query.filter_by(article_id=a.id, is_approved=True) \
                            .order_by(desc(Comment.created_at)).all()
    return render_template("news/article.html", article=a, related=related,
                           prev_article=prev_a, next_article=next_a, comments=comments,
                           preview_body=preview_body, is_preview=not current_user.is_authenticated)


@news_bp.route("/<slug>/comment", methods=["POST"])
@login_required
@csrf_required
def add_comment(slug):
    a = Article.query.filter_by(slug=slug).first_or_404()
    body = (request.form.get("content") or "").strip()
    if not body or len(body) < 2:
        flash("Comment too short.", "warning"); return redirect(url_for("news.article_detail", slug=slug))
    db.session.add(Comment(user_id=current_user.id, article_id=a.id,
                           content=sanitize_html(body)))
    db.session.commit()
    flash("Comment posted.", "success")
    return redirect(url_for("news.article_detail", slug=slug))


@news_bp.route("/<slug>/like", methods=["POST"])
@login_required
@csrf_required
def like(slug):
    a = Article.query.filter_by(slug=slug).first_or_404()
    existing = Like.query.filter_by(user_id=current_user.id, article_id=a.id).first()
    if existing:
        db.session.delete(existing); a.likes = max(0, (a.likes or 1) - 1)
        message = "Unliked."
    else:
        db.session.add(Like(user_id=current_user.id, article_id=a.id))
        a.likes = (a.likes or 0) + 1
        message = "Liked!"
    db.session.commit()
    flash(message, "info")
    return redirect(url_for("news.article_detail", slug=slug))
