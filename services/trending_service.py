"""Trending algorithm + recommendation engine."""
from datetime import datetime, timedelta
from sqlalchemy import desc
from app_modules.extensions import db
from app_modules.models import Article, Category


def recalc_trending(limit: int = 20):
    """Score = views*1 + likes*3 + shares*5 + recency_bonus."""
    try:
        cutoff = datetime.utcnow() - timedelta(days=3)
        arts = Article.query.filter(Article.published_at >= cutoff).all()
        for a in arts:
            age_h = max(1, (datetime.utcnow() - a.published_at).total_seconds() / 3600)
            recency = 100 / age_h
            a.trending_score = (a.views or 0) + (a.likes or 0) * 3 + (a.shares or 0) * 5 + recency
        arts.sort(key=lambda x: getattr(x, "trending_score", 0), reverse=True)
        for a in arts:
            a.is_trending = False
        for a in arts[:limit]:
            a.is_trending = True
        db.session.commit()
    except Exception:
        db.session.rollback()


def recommend_for_user(user, limit: int = 10):
    """Recommend based on favourite categories + recent reads."""
    if not user:
        return Article.query.order_by(desc(Article.published_at)).limit(limit).all()
    favs = [f.strip() for f in (user.favorite_categories or "").split(",") if f.strip()]
    recs = []
    if favs:
        recs = (Article.query
                .join(Category, Category.id == Article.category_id)
                .filter(Category.slug.in_(favs))
                .order_by(desc(Article.published_at)).limit(limit).all())
    if len(recs) < limit:
        seen = {r.id for r in recs}
        extras = (Article.query
                  .filter(~Article.id.in_(seen))
                  .order_by(desc(Article.published_at))
                  .limit(limit - len(recs)).all())
        recs.extend(extras)
    return recs
