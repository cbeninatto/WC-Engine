"""Vercel entrypoint — exposes the FastAPI app as an ASGI handler.

Vercel's Python runtime serves the module-level `app`. All routing is handled inside the
FastAPI app (see ../app.py); vercel.json sends every path here.

Required Vercel env vars (Project Settings -> Environment Variables):
  DATABASE_URL           Supabase pooler connection string  (REQUIRED -> Postgres backend)
  GITHUB_DISPATCH_TOKEN  token with actions:write  (so "Run monitor" fires the workflow
                         instead of trying to subprocess, which serverless can't do)
  GITHUB_REPO            "owner/repo", e.g. cbeninatto/WC-Engine
  ANTHROPIC_API_KEY      only if you ever run the agent server-side (normally via Actions)
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import app  # noqa: E402,F401  — FastAPI ASGI app picked up by Vercel
