from django.conf import settings
from django.contrib import admin
from django.contrib.auth.decorators import login_required
from django.urls import include, path
from django.views.static import serve

urlpatterns = [
    path("admin/", admin.site.urls),
    path("accounts/", include("django.contrib.auth.urls")),
    path("", include("melrater.core.urls")),
    # montages contain subject-derived images, so media requires login and is
    # served by Django itself (works with DEBUG off; fine at this tool's scale)
    path(
        "media/<path:path>",
        login_required(serve),
        {"document_root": settings.MEDIA_ROOT},
        name="media",
    ),
]
