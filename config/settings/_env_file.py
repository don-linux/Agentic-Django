"""
Load the root .env into the process environment, for development only.

config/settings/local.py imports this module before base.py, so base.py already
sees the values. A variable already set in the environment wins over the file.
DJANGO_ENV_FILE points at another file, and an empty value turns the read off
(the tests use that). Production and test settings never import this module:
production gets docker/prod/.env from Compose. See docs/settings.md#env-files.
"""

from __future__ import annotations

import os
from pathlib import Path

import environ

BASE_DIR = Path(__file__).resolve(strict=True).parent.parent.parent

ENV_FILE = os.environ.get("DJANGO_ENV_FILE", str(BASE_DIR / ".env"))
if ENV_FILE:
    environ.Env.read_env(ENV_FILE)
