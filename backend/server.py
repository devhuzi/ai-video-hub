"""Entry point kept for `uvicorn server:app` (see deploy/supervisord.conf).

The application lives in the `app` package; see app/main.py.
"""
from app.main import app  # noqa: F401
