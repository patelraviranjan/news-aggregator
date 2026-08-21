"""Vercel Python entrypoint.

Vercel's Python runtime looks for a WSGI/ASGI `app` object inside files
under /api. This just re-exports the same Flask app that `app.py` builds
for local/Docker use, so there's only one `create_app()` call site to keep
in sync -- vercel.json's catch-all route sends every request here.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import app  # noqa: E402  (the Flask app instance)
