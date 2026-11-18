"""
WSGI entry point; DJANGO_SETTINGS_MODULE defaults to production.

See docs/settings.md#settings-module-default.
"""

import os

from django.core.wsgi import get_wsgi_application

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings.production")

application = get_wsgi_application()
