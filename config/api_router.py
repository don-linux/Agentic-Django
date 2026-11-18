from rest_framework.routers import SimpleRouter

from apps.users.api.views import UserViewSet

# SimpleRouter in every environment: no API root view or format-suffix routes
# that would exist only with DEBUG=True.
router = SimpleRouter()

router.register("users", UserViewSet)


app_name = "api"
urlpatterns = router.urls
