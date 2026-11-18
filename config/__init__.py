"""The project package. Importing it loads the Celery app, so tasks bind to it."""

from .celery import app as celery_app

__all__ = ("celery_app",)
