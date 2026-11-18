from allauth.headless.constants import Client
from django.conf import settings
from django.conf.urls.static import static
from django.contrib import admin
from django.urls import include
from django.urls import path
from django.views.generic import RedirectView

from apps.users.views import PhoneView
from core.api.views import DocsView
from core.api.views import SchemaView

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
    path("api/v1/", include("config.api_router")),
    path("api/v1/schema/", SchemaView.as_view(), name="api-schema"),
    # The split view needs no inline script under SECURE_CSP.
    # See docs/security.md#content-security-policy.
    path(
        "api/v1/docs/",
        DocsView.as_view(url_name="api-schema"),
        name="api-docs",
    ),
]

# Same JSON body as the API. See docs/api.md#errors-outside-drf-views.
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
