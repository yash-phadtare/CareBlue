"""Vercel serverless entrypoint: exposes the Flask app.

Vercel's Python runtime imports this file for every request and serves
the WSGI `app` object. Keep it thin — all app setup lives in app.py.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ.setdefault('VERCEL', '1')

from app import app  # noqa: E402,F401  (Vercel looks for `app`)
