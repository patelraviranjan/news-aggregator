"""RSS / NewsAPI / GNews aggregator with deduplication + normalisation."""
import hashlib
import logging
import re
import time
from datetime import datetime
from typing import Iterable, List
from urllib.parse import urljoin, urlparse

import feedparser
import requests

log = logging.getLogger(__name__)

# Pretend to be a normal browser -- many news sites (Aaj Tak, OnlineKhabar,
# Hindustan, etc.) block the default python-requests user-agent.
_BROWSER_HEADERS = {
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/124.0.0.0 Safari/537.36"),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}

# Common feed paths tried against any homepage that doesn't advertise
# a <link rel="alternate"> feed in its <head>.
_COMMON_FEED_PATHS = [
    "/feed", "/feed/", "/rss", "/rss/", "/rss.xml", "/atom.xml",
    "/feeds/posts/default", "/rssfeeds/?id=home", "/feed/rss",
    "/index.xml",
]


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------

def _slugify(text: str) -> str:
    import re
    text = (text or "").lower()[:200]
    return re.sub(r"[^a-z0-9]+", "-", text).strip("-") or "article"


def _content_hash(title: str, source: str) -> str:
    return hashlib.sha1(f"{(title or '').strip().lower()}|{source}".encode()).hexdigest()


def reading_time(text: str) -> int:
    words = max(1, len((text or "").split()))
    return max(1, words // 200)


def normalise(raw: dict) -> dict:
    """Turn a raw feed/API item into a uniform shape."""
    title = raw.get("title", "").strip()
    summary = raw.get("summary") or raw.get("description") or ""
    image = (raw.get("image_url")
             or (raw.get("image") or {}).get("url")
             or raw.get("urlToImage")
             or raw.get("thumbnail")
             or "")
    published = raw.get("published_at")
    if isinstance(published, str):
        try:
            published = datetime.fromisoformat(published.replace("Z", "+00:00"))
        except Exception:
            published = datetime.utcnow()
    elif isinstance(published, time.struct_time):
        # feedparser hands back entry.published_parsed as a struct_time --
        # SQLite/SQLAlchemy can't store that directly, it needs a real datetime.
        try:
            published = datetime(*published[:6])
        except Exception:
            published = datetime.utcnow()
    elif not isinstance(published, datetime):
        published = datetime.utcnow()
    return {
        "title": title,
        "summary": summary.strip(),
        "image_url": image.strip(),
        "url": raw.get("url") or raw.get("link") or "",
        "author": (raw.get("author") or "").strip(),
        "published_at": published,
        "source_name": raw.get("source_name") or raw.get("source") or "",
        "reading_time": reading_time(summary),
    }


# ------------------------------------------------------------------
# Public fetchers
# ------------------------------------------------------------------

def fetch_rss(url: str, source_name: str, limit: int = 30) -> Iterable[dict]:
    """Parse an RSS feed safely and yield normalised items.

    Pass limit=None to pull every entry the feed provides instead of
    capping at a fixed count.
    """
    if not url:
        return []
    try:
        feed = feedparser.parse(url)
    except Exception as ex:
        log.warning("RSS parse error for %s: %s", url, ex)
        return []

    entries = []
    for entry in (feed.entries if limit is None else feed.entries[:limit]):
        image = _extract_entry_image(entry)

        entries.append(normalise({
            "title": entry.get("title", ""),
            "summary": entry.get("summary", ""),
            "url": entry.get("link", ""),
            "image": {"url": image} if image else None,
            "author": entry.get("author", ""),
            "published_at": entry.get("published_parsed"),
            "source_name": source_name,
        }))
    return entries


_IMG_SRC_RE = re.compile(r'<img[^>]+src=["\']([^"\'>]+)["\']', re.IGNORECASE)


def _extract_entry_image(entry) -> str:
    """Pull a usable image URL out of a feedparser entry.

    Different publishers put the image in different places:
      - <media:content url="..."> -> entry.media_content
      - <media:thumbnail url="..."> -> entry.media_thumbnail (NOT the same
        field as media_content -- feedparser keeps them separate, and the
        old code only ever checked media_content, so any feed that only
        supplies a thumbnail (common for Aaj Tak/Zee/OnlineKhabar/NPR/TV
        Annapurna) fell through with no image at all).
      - <enclosure url="..." type="image/*">
      - or, most commonly for Indian/Nepali outlets, an inline <img> tag
        buried inside the HTML of <description>/<content:encoded>, which
        none of the structured fields above capture.
    Tries each in order and falls back to scanning the HTML body last.
    """
    if getattr(entry, "media_content", None):
        try:
            url = entry.media_content[0].get("url", "")
            if url:
                return url
        except Exception:
            pass

    if getattr(entry, "media_thumbnail", None):
        try:
            url = entry.media_thumbnail[0].get("url", "")
            if url:
                return url
        except Exception:
            pass

    if getattr(entry, "enclosures", None):
        for enc in entry.enclosures:
            if enc.get("type", "").startswith("image/"):
                return enc.get("href", "")

    # Fallback: scrape the first <img src="..."> out of the entry's HTML
    # body. `content` (content:encoded) is usually the fuller version;
    # `summary`/`description` is the short one -- check both.
    html_blobs = []
    if getattr(entry, "content", None):
        try:
            html_blobs.append(entry.content[0].get("value", ""))
        except Exception:
            pass
    if entry.get("summary"):
        html_blobs.append(entry.get("summary"))

    for blob in html_blobs:
        match = _IMG_SRC_RE.search(blob or "")
        if match:
            return match.group(1)

    return ""


_OG_IMAGE_RE = re.compile(
    r'<meta[^>]+(?:property|name)=["\']og:image(?::secure_url)?["\'][^>]+content=["\']([^"\']+)["\']',
    re.IGNORECASE,
)
_TWITTER_IMAGE_RE = re.compile(
    r'<meta[^>]+name=["\']twitter:image(?::src)?["\'][^>]+content=["\']([^"\']+)["\']',
    re.IGNORECASE,
)


# ------------------------------------------------------------------
# Auto-categorisation
# ------------------------------------------------------------------
# RSS/GNews items don't come with a category label the way NewsAPI's
# top-headlines endpoint does -- so without this, every RSS-sourced
# article (i.e. almost everything, since RSS is the primary pipeline)
# was landing with category_id=NULL. That silently emptied out
# /category/<slug> pages, the homepage's per-category sections, and
# admin category counts for anything other than the optional NewsAPI
# feed. Keyword match against title+summary, ordered by specificity so
# e.g. a "politics" story mentioning a stadium isn't misfiled as sports.
_CATEGORY_KEYWORDS = [
    ("health", (
        "health", "covid", "vaccine", "hospital", "disease", "doctor",
        "medical", "medicine", "cancer", "virus", "outbreak",
        "mental health", "diet", "fitness", "surgery", "patient",
        "diagnosis", "treatment",
    )),
    ("technology", (
        "tech", "ai", "artificial intelligence", "software", "app",
        "smartphone", "iphone", "android", "chip", "startup", "cyber",
        "gadget", "computer", "internet", "robot", "microsoft", "google",
        "apple", "meta", "amazon", "nvidia", "elon musk", "spacex",
        "crypto", "bitcoin", "programming",
    )),
    ("science", (
        "nasa", "space", "planet", "asteroid", "physics", "climate change",
        "scientists", "telescope", "biology", "genome", "fossil",
        "quantum", "astronomy",
    )),
    ("sports", (
        "cricket", "football", "soccer", "olympics", "tennis", "nba",
        "nfl", "match", "tournament", "coach", "goal", "world cup",
        "premier league", "athlete", "medal", "wimbledon", "ipl",
    )),
    ("entertainment", (
        "movie", "film", "actor", "actress", "hollywood", "bollywood",
        "celebrity", "music", "album", "concert", "netflix",
        "box office", "oscar", "grammy", "singer",
    )),
    ("business", (
        "stock", "market", "economy", "trade", "inflation", "gdp",
        "shares", "ipo", "merger", "earnings", "ceo", "revenue",
        "bank", "investment", "wall street",
    )),
    ("politics", (
        "election", "president", "government", "minister", "parliament",
        "senate", "congress", "policy", "vote", "campaign", "lawmaker",
        "prime minister", "diplomat", "sanctions",
    )),
    ("local", (
        "municipal", "city council", "neighborhood",
    )),
]

# Multi-word phrases need \s+ between words; everything is matched as a
# whole word/phrase (\b...\b) so short tokens like "ai" or "who" don't
# false-positive inside unrelated words (e.g. the plain-substring version
# of this matched "nfl" inside "inflation", misfiling business news as
# sports -- word boundaries close that off).
_CATEGORY_PATTERNS = [
    (slug, re.compile(r"\b" + r"\s+".join(re.escape(w) for w in kw.split()) + r"\b"))
    for slug, keywords in _CATEGORY_KEYWORDS for kw in keywords
]
_CATEGORY_ORDER = list(dict.fromkeys(slug for slug, _ in _CATEGORY_KEYWORDS))


def guess_category(title: str, summary: str = "") -> str:
    """Best-effort category slug for an article with no explicit category.
    Falls back to 'world', which is always safe since it's one of the
    default seeded categories.
    """
    text = f"{(title or '').lower()} {(summary or '').lower()}"
    hits = {}
    for slug, pattern in _CATEGORY_PATTERNS:
        if pattern.search(text):
            hits[slug] = hits.get(slug, 0) + 1
    if not hits:
        return "world"
    # Category with the most keyword hits wins; ties broken by the fixed
    # priority order above (health/tech checked before broader buckets).
    best = max(_CATEGORY_ORDER, key=lambda s: (hits.get(s, 0), -_CATEGORY_ORDER.index(s)))
    return best if hits.get(best) else "world"


def enrich_missing_image(article_url: str, timeout: float = 2.5) -> str:
    """Last-resort image lookup: open the actual article page and read its
    og:image / twitter:image meta tag. Only called for items the feed itself
    gave no image for -- this is what fixes sources (many Indian/Nepali
    outlets, Al Jazeera included) whose RSS entries carry no media tag at all
    even though the article page itself has a perfectly good hero image.
    Kept cheap and bounded: small timeout, only reads the first chunk of
    HTML (the <head> is always first), never raises.
    """
    if not article_url:
        return ""
    try:
        resp = requests.get(
            article_url, headers=_BROWSER_HEADERS, timeout=timeout, stream=True
        )
        chunk = next(resp.iter_content(65536, decode_unicode=False), b"")
        html = chunk.decode("utf-8", errors="ignore")
        resp.close()
    except Exception as ex:
        log.info("enrich_missing_image failed for %s: %s", article_url, ex)
        return ""

    match = _OG_IMAGE_RE.search(html) or _TWITTER_IMAGE_RE.search(html)
    if match:
        return urljoin(article_url, match.group(1))
    return ""


# ------------------------------------------------------------------
# Feed submission / discovery -- turn "any website" into a usable RSS source
# ------------------------------------------------------------------

_LINK_TAG_RE = re.compile(
    r"<link\b[^>]*?rel=[\"'](?:alternate)[\"'][^>]*?>", re.IGNORECASE
)
_TYPE_RE = re.compile(r"type=[\"'](application/(?:rss|atom)\+xml)[\"']", re.IGNORECASE)
_HREF_RE = re.compile(r"href=[\"']([^\"']+)[\"']", re.IGNORECASE)
_TITLE_ATTR_RE = re.compile(r"title=[\"']([^\"']+)[\"']", re.IGNORECASE)


def _normalise_candidate_url(raw_url: str) -> str:
    """Accept bare domains ('bbc.com') as well as full URLs."""
    raw_url = (raw_url or "").strip()
    if not raw_url:
        return ""
    if not re.match(r"^https?://", raw_url, re.IGNORECASE):
        raw_url = "https://" + raw_url
    return raw_url


def _extract_feed_links_from_html(html: str, base_url: str) -> List[dict]:
    """Find <link rel="alternate" type="application/rss+xml" ...> tags."""
    found = []
    for tag in _LINK_TAG_RE.findall(html):
        type_match = _TYPE_RE.search(tag)
        href_match = _HREF_RE.search(tag)
        if not type_match or not href_match:
            continue
        title_match = _TITLE_ATTR_RE.search(tag)
        found.append({
            "url": urljoin(base_url, href_match.group(1)),
            "title": title_match.group(1) if title_match else "",
        })
    return found


def probe_feed(url: str, limit_preview: int = 5) -> dict | None:
    """Try to parse `url` as a feed. Returns a preview dict if it's valid, else None."""
    try:
        resp = requests.get(url, headers=_BROWSER_HEADERS, timeout=8)
        if resp.status_code >= 400:
            return None
        parsed = feedparser.parse(resp.content)
    except Exception as ex:
        log.info("probe_feed failed for %s: %s", url, ex)
        return None

    if not parsed.entries:
        return None

    feed_title = (parsed.feed.get("title") or "").strip()
    samples = [e.get("title", "").strip() for e in parsed.entries[:limit_preview] if e.get("title")]
    return {
        "feed_url": url,
        "feed_title": feed_title,
        "site_link": (parsed.feed.get("link") or "").strip(),
        "entry_count": len(parsed.entries),
        "sample_titles": samples,
    }


def name_from_url(url: str) -> str:
    """Derive a readable fallback name from a URL's domain, e.g.
    'https://www.aajtak.in/rssfeeds/?id=home' -> 'Aajtak'. Used when a
    source is added from a bare RSS URL with no name typed in and the
    feed itself didn't carry a usable <title>."""
    try:
        host = urlparse(url).netloc.lower()
    except Exception:
        return "Untitled Source"
    host = re.sub(r"^www\.", "", host)
    host = host.split(":")[0]
    base = host.split(".")[0] if host else ""
    return base.capitalize() if base else "Untitled Source"


def resolve_new_source(name: str, url: str, rss_url: str) -> dict:
    """Fill in whatever's missing (name / website url) when a source is
    submitted with only an RSS feed URL, so 'just paste the RSS URL' is
    enough to add and start fetching a site -- no separate discovery step
    required. Always probes the feed once, both to backfill the blanks and
    to confirm the URL is actually a working feed before we save it.
    """
    name = (name or "").strip()
    url = (url or "").strip()
    rss_url = (rss_url or "").strip()

    preview = probe_feed(rss_url) if rss_url else None
    if not name:
        if preview and preview.get("feed_title"):
            name = preview["feed_title"]
        else:
            name = name_from_url(rss_url)
    if not url:
        if preview and preview.get("site_link"):
            url = preview["site_link"]
        else:
            parsed = urlparse(rss_url)
            url = f"{parsed.scheme}://{parsed.netloc}" if parsed.scheme else ""

    return {"name": name, "url": url, "rss_url": rss_url, "preview": preview}


def discover_feeds(website_url: str, max_candidates: int = 6) -> dict:
    """Given ANY website URL (BBC, Aaj Tak, OnlineKhabar, Hindustan, ...),
    find working RSS/Atom feed(s) on it.

    Strategy:
      1. If the URL already looks like a feed, probe it directly.
      2. Otherwise fetch the homepage HTML and read <link rel="alternate"> tags.
      3. Fall back to a list of common feed paths (/feed, /rss.xml, ...).

    Returns {"site_url": ..., "candidates": [ {feed_url, feed_title, entry_count, sample_titles}, ... ]}
    """
    site_url = _normalise_candidate_url(website_url)
    if not site_url:
        return {"site_url": "", "candidates": []}

    seen = set()
    candidates = []

    def _try(url):
        if not url or url in seen or len(candidates) >= max_candidates:
            return
        seen.add(url)
        preview = probe_feed(url)
        if preview:
            candidates.append(preview)

    # 1) Maybe they already pasted a direct feed URL.
    _try(site_url)

    if not candidates:
        # 2) Look for declared feeds in the page <head>.
        try:
            resp = requests.get(site_url, headers=_BROWSER_HEADERS, timeout=8)
            html = resp.text if resp.status_code < 400 else ""
        except Exception as ex:
            log.info("discover_feeds: could not fetch %s (%s)", site_url, ex)
            html = ""

        for link in _extract_feed_links_from_html(html, site_url):
            _try(link["url"])

        # 3) Try common feed paths on the same domain.
        if not candidates:
            parsed = urlparse(site_url)
            root = f"{parsed.scheme}://{parsed.netloc}"
            for path in _COMMON_FEED_PATHS:
                _try(urljoin(root, path))

    return {"site_url": site_url, "candidates": candidates}


def fetch_newsapi(api_key: str, category: str = "general", country: str = "us", limit: int = 20):
    if not api_key:
        return []
    try:
        url = "https://newsapi.org/v2/top-headlines"
        params = {"apiKey": api_key, "pageSize": limit, "category": category, "country": country}
        r = requests.get(url, params=params, timeout=10)
        if r.status_code != 200:
            return []
        for a in r.json().get("articles", []):
            yield normalise({
                "title": a.get("title"),
                "summary": a.get("description"),
                "url": a.get("url"),
                "image_url": a.get("urlToImage"),
                "author": a.get("author"),
                "published_at": a.get("publishedAt"),
                "source_name": (a.get("source") or {}).get("name", "NewsAPI"),
            })
    except Exception as ex:
        log.warning("NewsAPI error: %s", ex)
        return []


def fetch_gnews(api_key: str, category: str = "general", limit: int = 20):
    if not api_key:
        return []
    try:
        url = "https://gnews.io/api/v4/top-headlines"
        params = {"token": api_key, "lang": "en", "topic": category, "max": limit}
        r = requests.get(url, params=params, timeout=10)
        if r.status_code != 200:
            return []
        for a in r.json().get("articles", []):
            yield normalise({
                "title": a.get("title"),
                "summary": a.get("description"),
                "url": a.get("url"),
                "image_url": a.get("image"),
                "author": a.get("source", {}).get("name", "GNews"),
                "published_at": a.get("publishedAt"),
                "source_name": "GNews",
            })
    except Exception as ex:
        log.warning("GNews error: %s", ex)
        return []


# ------------------------------------------------------------------
# Persistence (caller passes app context)
# ------------------------------------------------------------------

def persist(app, items: List[dict], category_slug: str = None, source_model=None,
            mark_trending: bool = False, max_image_enrich: int = 3):
    """Insert articles with deduplication by content hash stored in slug."""
    from app_modules.extensions import db
    from app_modules.models import Article, Category

    cat = None
    if category_slug:
        cat = Category.query.filter_by(slug=category_slug).first()

    # Pre-load all categories once (not per-item) for the auto-classifier
    # fallback below, keyed by slug for O(1) lookup.
    all_categories = {c.slug: c for c in Category.query.all()}

    inserted = 0
    enriched_count = 0
    for it in items:
        if not it.get("title"):
            continue

        item_cat = cat
        if item_cat is None and all_categories:
            guessed_slug = guess_category(it.get("title", ""), it.get("summary", ""))
            item_cat = all_categories.get(guessed_slug)

        # If the feed itself didn't carry an image, try to pull one from the
        # article's own og:image tag. Bounded to max_image_enrich per batch
        # so a slow/unreachable site can't stall the whole fetch cycle.
        if not it.get("image_url") and enriched_count < max_image_enrich:
            found = enrich_missing_image(it.get("url", ""))
            if found:
                it["image_url"] = found
            enriched_count += 1
        slug = _slugify(it["title"])
        # Stronger dedup via slug + content hash to avoid collision on short titles
        slug = f"{slug}-{_content_hash(it['title'], it.get('source_name', ''))[:8]}"

        # Autoflush disabled here: if a *previous* item in this batch is bad
        # and hasn't been caught yet, a plain query would trigger autoflush,
        # try to insert it, and poison the whole session before we even get
        # to check this item's slug.
        with db.session.no_autoflush:
            if Article.query.filter_by(slug=slug).first():
                continue

        published_at = it.get("published_at")
        if not isinstance(published_at, datetime):
            published_at = datetime.utcnow()

        a = Article(
            slug=slug,
            title=it["title"][:480],
            summary=(it.get("summary") or "")[:1000],
            content=it.get("summary") or "",
            image_url=it.get("image_url"),
            url=it.get("url"),
            author=it.get("author"),
            published_at=published_at,
            fetched_at=datetime.utcnow(),
            source_id=source_model.id if source_model is not None else None,
            source_name=(source_model.name if source_model is not None
                         else it.get("source_name") or "Wire"),
            category_id=item_cat.id if item_cat else None,
            reading_time=it.get("reading_time", 3),
            is_trending=mark_trending,
        )
        db.session.add(a)

        # Flush (not commit) each item individually so one bad row only
        # rolls back itself, not the whole batch already staged in this session.
        try:
            db.session.flush()
            inserted += 1
        except Exception as ex:
            db.session.rollback()
            log.warning("Skipped one article (%s): %s", it.get("title", "")[:60], ex)

    try:
        db.session.commit()
    except Exception as ex:
        db.session.rollback()
        log.warning("persist commit failed: %s", ex)
        return inserted

    if inserted:
        try:
            from app_modules.extensions import cache
            cache.clear()
        except Exception:
            pass
    return inserted
