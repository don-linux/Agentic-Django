"""The project API with every app's router mounted, served at /api/v1/.

Routers are added here, in a module Python runs once per process, and not in
config/urls.py: a test that reloads the URLconf would call add_router() again,
and Ninja refuses a router added after api.urls was built.
"""

from apps.users.api.views import router as users_router
from core.api.api import api

api.add_router("/users/", users_router)

__all__ = ["api"]
