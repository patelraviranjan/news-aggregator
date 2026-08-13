# 📰 News Aggregator

A production-ready, full-stack news aggregator website built with **Flask**, inspired by BBC News, Google News, Inshorts and Microsoft Start.

![Python](https://img.shields.io/badge/Python-3.11-blue) ![Flask](https://img.shields.io/badge/Flask-3.0-green) ![License](https://img.shields.io/badge/license-MIT-orange)

## ✨ Features

- 🏠 Modern homepage with hero banner, breaking news ticker, top headlines, trending, latest, editor's picks, recommended
- 📰 30+ pages (categories, breaking, trending, search, saved, history, profile, settings, admin, legal, errors)
- 🔐 JWT auth, Bcrypt hashing, Google OAuth-ready, CSRF, XSS, rate limiting, security headers (Talisman)
- 🤖 Auto RSS / NewsAPI / GNews fetching every 15 min with deduplication + trending algorithm
- 💾 Bookmarks, reading history, likes, comments, profile picture upload, dark mode, language selector
- 📊 Admin dashboard with analytics, user/article/source management
- 📱 Fully responsive (desktop / tablet / mobile), Bootstrap 5 + AOS + Swiper + Chart.js
- 🚀 Caching, pagination, lazy-load, gzip-compressed static, Redis-ready

## 🏗 Project Structure

```
news-aggregator/
├── backend/
│   ├── app.py                      # entrypoint
│   ├── application.py              # application factory + extension wiring
│   ├── config.py                   # environment-driven configuration
│   ├── requirements.txt
│   ├── Dockerfile
│   ├── .env.example
│   ├── app_modules/
│   │   ├── __init__.py             # blueprint registration
│   │   ├── extensions.py           # shared Flask extensions
│   │   ├── models.py               # all 11 SQLAlchemy models
│   │   ├── scheduler.py            # APScheduler RSS fetcher
│   │   └── security.py             # bcrypt / JWT helpers
│   ├── routes/
│   │   ├── public_routes.py        # homepage & section pages
│   │   ├── auth_routes.py          # login / register / forgot
│   │   ├── news_routes.py          # article view / search
│   │   ├── user_routes.py          # profile / settings / bookmarks / history
│   │   ├── admin_routes.py         # admin dashboard & CRUD
│   │   └── api_routes.py           # JSON REST API
│   ├── services/
│   │   ├── news_fetcher.py         # RSS / NewsAPI / GNews pull
│   │   └── trending_service.py     # trending + recommendation
│   ├── static/                     # CSS, JS, images, uploads
│   └── templates/
│       ├── layout/                 # base.html, header.html, footer.html
│       ├── auth/                   # login, register, forgot
│       ├── admin/                  # dashboard, manage panels
│       ├── news/                   # article.html, category.html
│       ├── errors/                 # 404, 500
│       └── partials/               # cards, ticker, swipers
└── frontend/                       # public-facing static assets (mirrored)
```

## 🚀 Quick start

### 1. Local (SQLite, no API keys needed)

```bash
cd backend
python -m venv venv && source venv/bin/activate          # Windows: venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env  # NewsAPI / GNews keys optional; RSS works offline
python app.py
```

Open http://127.0.0.1:5000

### 2. Seed data

On first start the app:
- creates all tables
- seeds an admin (`admin@news.local` / `admin123`)
- seeds default categories & sources
- starts the background RSS feed scheduler

### 3. Docker

```bash
docker-compose up --build
```

### 4. Default Admin

> email: `admin@news.local`
> password: `admin123`
> change immediately in production

## 🛡 Security

- JWT (Flask-JWT-Extended) for stateless API auth
- Bcrypt for password hashing
- Talisman enforces HTTPS, HSTS, secure CSP headers
- Flask-Limiter rate-limits sensitive endpoints
- Bleach sanitises rendered user content
- CSRF on all POST forms (Flask-WTF pattern via custom token)
- SQL injection prevented via SQLAlchemy ORM

## 📜 License

MIT
