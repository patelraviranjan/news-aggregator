"""Make services importable as Python package."""
from services import news_fetcher, trending_service

__all__ = ["news_fetcher", "trending_service"]
