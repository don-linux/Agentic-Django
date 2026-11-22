from allauth.headless.constants import Client
from django.conf import settings
from django.conf.urls.static import static
from django.contrib import admin
from django.urls import include
from django.urls import path
from django.views.generic import RedirectView

from apps.users.views import PhoneView
from config.api import api
from core.api.negotiation import docs_view
from core.api.negotiation import schema_view
from core.api.negotiation import staff_docs_required

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
    path("api/v1/", api.urls),
    path("api/v1/schema/", staff_docs_required(schema_view), name="api-schema"),
    path("api/v1/docs/", staff_docs_required(docs_view), name="api-docs"),
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
