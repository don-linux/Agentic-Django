from allauth.headless.constants import Client
from django.conf import settings
from django.conf.urls.static import static
from django.contrib import admin
from django.urls import include
from django.urls import path
from django.views.generic import RedirectView

from apps.users.views import PhoneView
from config.api import api

urlpatterns = [
    path(settings.ADMIN_URL, admin.site.urls),
    # Before allauth's include, which mounts the same path: no 500 on methods
    # allauth leaves unhandled. See docs/auth/phone.md.
    *(
        path(
            f"_allauth/{client}/v1/account/phone",
            PhoneView.as_api_view(client=Client(client)),
        )
        for client in settings.HEADLESS_CLIENTS
    ),
    path("_allauth/", include("allauth.headless.urls")),
    *static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT),
]

urlpatterns += [
    # Ninja serves the schema and the docs under it too (core/api/api.py).
    path("api/v1/", api.urls),
]

# Same JSON body as the API. See docs/api.md#errors-outside-api-views.
handler400 = "core.errors.bad_request"
handler403 = "core.errors.permission_denied"
handler404 = "core.errors.page_not_found"
handler500 = "core.errors.server_error"

if settings.DEBUG:
    urlpatterns += [
        path(
            "",
            RedirectView.as_view(url=f"/{settings.ADMIN_URL}", permanent=False),
        ),
    ]
