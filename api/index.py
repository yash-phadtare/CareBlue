"""Vercel serverless entrypoint: exposes the Flask app.

Vercel's Python runtime imports this file for every request and serves
the WSGI `app` object. Keep it thin — all app setup lives in app.py.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ.setdefault('VERCEL', '1')

from app import app as flask_app  # noqa: E402  (must come after VERCEL is set)

# Vercel routes every request to this file via the vercel.json rewrite, and
# (per the build warning) the function may see the rewritten destination path
# (/api/index...) instead of the original URL. Restore the real path so Flask
# routing matches. No-op when Vercel already passes the original path.
_orig_wsgi = flask_app.wsgi_app


def _vercel_wsgi(environ, start_response):
    path = environ.get('PATH_INFO', '') or ''
    if path == '/api/index':
        environ['PATH_INFO'] = '/'
    elif path.startswith('/api/index/'):
        environ['PATH_INFO'] = path[len('/api/index'):]
    return _orig_wsgi(environ, start_response)


flask_app.wsgi_app = _vercel_wsgi

app = flask_app  # Vercel looks for `app`
