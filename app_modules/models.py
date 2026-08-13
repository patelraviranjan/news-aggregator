"""SQLAlchemy models — 11 tables covering the full spec."""
from datetime import datetime
from flask_login import UserMixin
from werkzeug.security import generate_password_hash, check_password_hash
from app_modules.extensions import db, bcrypt


# -------------------- Helpers --------------------

def _hash_pw(raw: str) -> str:
    return bcrypt.generate_password_hash(raw).decode("utf-8")


# -------------------- Users --------------------

class User(db.Model, UserMixin):
    __tablename__ = "users"
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False)
    email = db.Column(db.String(150), unique=True, nullable=False)
    password_hash = db.Column(db.String(255), nullable=False)
    full_name = db.Column(db.String(150))
    phone = db.Column(db.String(30))
    address = db.Column(db.String(300))
    avatar = db.Column(db.String(255), default="images/default-avatar.png")
    bio = db.Column(db.String(500))
    country = db.Column(db.String(80))
    language = db.Column(db.String(20), default="en")
    favorite_categories = db.Column(db.String(500))  # csv
    email_verified = db.Column(db.Boolean, default=False)
    is_active_flag = db.Column(db.Boolean, default=True)
    role = db.Column(db.String(20), default="user")  # user | admin
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    last_seen = db.Column(db.DateTime, default=datetime.utcnow)
    push_enabled = db.Column(db.Boolean, default=True)

    # Flask-Login expects is_active
    @property
    def is_active(self):
        return bool(self.is_active_flag)

    def set_password(self, raw):
        self.password_hash = _hash_pw(raw)

    def check_password(self, raw):
        try:
            return bcrypt.check_password_hash(self.password_hash, raw)
        except Exception:
            return False

    def to_dict(self):
        return {
            "id": self.id,
            "username": self.username,
            "email": self.email,
            "full_name": self.full_name,
            "avatar": self.avatar,
            "country": self.country,
            "language": self.language,
            "favorite_categories": self.favorite_categories,
            "role": self.role,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }


# -------------------- Admins --------------------

class Admin(db.Model, UserMixin):
    __tablename__ = "admins"
    id = db.Column(db.Integer, primary_key=True)
    email = db.Column(db.String(150), unique=True, nullable=False)
    password_hash = db.Column(db.String(255), nullable=False)
    name = db.Column(db.String(150), nullable=False)
    role = db.Column(db.String(50), default="superadmin")  # superadmin | editor | moderator
    avatar = db.Column(db.String(255), default="images/default-avatar.png")
    address = db.Column(db.String(300))
    country = db.Column(db.String(80))
    phone = db.Column(db.String(30))
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    last_login = db.Column(db.DateTime)

    # Flask-Login expects is_active; admins are always active
    @property
    def is_active(self):
        return True

    # Prefix so the session loader can tell Admin ids apart from User ids
    def get_id(self):
        return f"admin-{self.id}"

    def set_password(self, raw):
        self.password_hash = bcrypt.generate_password_hash(raw).decode("utf-8")

    def check_password(self, raw):
        try:
            return bcrypt.check_password_hash(self.password_hash, raw)
        except Exception:
            return False


# -------------------- Categories --------------------

class Category(db.Model):
    __tablename__ = "categories"
    id = db.Column(db.Integer, primary_key=True)
    slug = db.Column(db.String(80), unique=True, nullable=False)
    name = db.Column(db.String(120), nullable=False)
    icon = db.Column(db.String(80), default="bi-newspaper")
    color = db.Column(db.String(20), default="#2563eb")
    description = db.Column(db.String(300))
    is_active = db.Column(db.Boolean, default=True)
    sort_order = db.Column(db.Integer, default=0)


# -------------------- Sources --------------------

class Source(db.Model):
    __tablename__ = "sources"
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(150), nullable=False)
    url = db.Column(db.String(500))
    rss_url = db.Column(db.String(500))
    country = db.Column(db.String(50), default="global")
    logo = db.Column(db.String(500))
    is_active = db.Column(db.Boolean, default=True)
    reliability = db.Column(db.Integer, default=80)  # 0-100 trust score
    created_at = db.Column(db.DateTime, default=datetime.utcnow)


# -------------------- Articles --------------------

class Article(db.Model):
    __tablename__ = "articles"
    id = db.Column(db.Integer, primary_key=True)
    title = db.Column(db.String(500), nullable=False)
    slug = db.Column(db.String(500), unique=True, nullable=False, index=True)
    summary = db.Column(db.Text)
    content = db.Column(db.Text)
    image_url = db.Column(db.String(800))
    source_id = db.Column(db.Integer, db.ForeignKey("sources.id"), index=True)
    source_name = db.Column(db.String(150))  # denormalised for fast reads
    category_id = db.Column(db.Integer, db.ForeignKey("categories.id"), index=True)
    author = db.Column(db.String(150))
    url = db.Column(db.String(800))
    published_at = db.Column(db.DateTime, default=datetime.utcnow, index=True)
    fetched_at = db.Column(db.DateTime, default=datetime.utcnow)
    language = db.Column(db.String(20), default="en")
    country = db.Column(db.String(50), default="global")
    is_breaking = db.Column(db.Boolean, default=False, index=True)
    is_trending = db.Column(db.Boolean, default=False, index=True)
    views = db.Column(db.Integer, default=0)
    likes = db.Column(db.Integer, default=0)
    shares = db.Column(db.Integer, default=0)
    reading_time = db.Column(db.Integer, default=3)  # minutes

    source = db.relationship("Source", backref="articles", lazy=True)
    category = db.relationship("Category", backref="articles", lazy=True)

    def to_dict(self):
        return {
            "id": self.id,
            "title": self.title,
            "slug": self.slug,
            "summary": self.summary or "",
            "image_url": self.image_url or "",
            "source": self.source_name,
            "category": self.category.name if self.category else None,
            "author": self.author,
            "published_at": self.published_at.isoformat() if self.published_at else None,
            "is_breaking": self.is_breaking,
            "is_trending": self.is_trending,
            "views": self.views,
            "likes": self.likes,
            "reading_time": self.reading_time,
            "url": self.url or "",
        }


# -------------------- Bookmarks --------------------

class Bookmark(db.Model):
    __tablename__ = "bookmarks"
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False, index=True)
    article_id = db.Column(db.Integer, db.ForeignKey("articles.id"), nullable=False, index=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    __table_args__ = (db.UniqueConstraint("user_id", "article_id"),)


# -------------------- Reading History --------------------

class ReadingHistory(db.Model):
    __tablename__ = "reading_history"
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False, index=True)
    article_id = db.Column(db.Integer, db.ForeignKey("articles.id"), nullable=False, index=True)
    progress = db.Column(db.Integer, default=0)  # percent
    read_at = db.Column(db.DateTime, default=datetime.utcnow, index=True)


# -------------------- Comments --------------------

class Comment(db.Model):
    __tablename__ = "comments"
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    article_id = db.Column(db.Integer, db.ForeignKey("articles.id"), nullable=False, index=True)
    content = db.Column(db.Text, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, index=True)
    is_approved = db.Column(db.Boolean, default=True)

    user = db.relationship("User", lazy=True)


# -------------------- Likes --------------------

class Like(db.Model):
    __tablename__ = "likes"
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    article_id = db.Column(db.Integer, db.ForeignKey("articles.id"), nullable=False, index=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    __table_args__ = (db.UniqueConstraint("user_id", "article_id"),)


# -------------------- Notifications --------------------

class Notification(db.Model):
    __tablename__ = "notifications"
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False, index=True)
    title = db.Column(db.String(255), nullable=False)
    body = db.Column(db.Text)
    icon = db.Column(db.String(80), default="bi-bell")
    link = db.Column(db.String(500))
    is_read = db.Column(db.Boolean, default=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, index=True)


# -------------------- Reports --------------------

class Report(db.Model):
    __tablename__ = "reports"
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"))
    article_id = db.Column(db.Integer, db.ForeignKey("articles.id"), index=True)
    reason = db.Column(db.String(255))
    details = db.Column(db.Text)
    status = db.Column(db.String(20), default="open")  # open | reviewing | resolved
    created_at = db.Column(db.DateTime, default=datetime.utcnow, index=True)


# -------------------- Logs --------------------

class ActivityLog(db.Model):
    __tablename__ = "activity_logs"
    id = db.Column(db.Integer, primary_key=True)
    actor = db.Column(db.String(150))
    action = db.Column(db.String(255))
    target = db.Column(db.String(255))
    ip = db.Column(db.String(64))
    created_at = db.Column(db.DateTime, default=datetime.utcnow, index=True)
