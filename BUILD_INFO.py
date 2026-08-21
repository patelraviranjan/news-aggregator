"""Bump BUILD_TAG any time you hand off a new zip/commit. Shown in
/api/status and in the on-page config-issue banner so it's possible to
confirm at a glance whether a given fix has actually gone live on Vercel,
instead of guessing from banner text alone (env var propagation timing,
stale CDN/browser caches, and deploying the wrong branch/zip can all make
"I redeployed" and "the new code is live" different things).
"""
BUILD_TAG = "2026-08-21-env-secret-cleanup-v3"
