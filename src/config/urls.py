from django.contrib import admin
from django.contrib.auth import views as auth_views
from django.urls import include, path

from melrater.core import views

urlpatterns = [
    path("admin/", admin.site.urls),
    # Login and logout only, rather than include("django.contrib.auth.urls").
    # Reviewers are issued a password by `create_rater` and never set their own,
    # so nothing routes password_change or password_reset. Leaving them routed
    # was actively harmful: /accounts/password_reset/ rendered
    # django.contrib.admin's own template to anonymous visitors — admin branding
    # and all — and then 500'd on submit, because no mail backend exists.
    path("accounts/login/", auth_views.LoginView.as_view(), name="login"),
    path("accounts/logout/", auth_views.LogoutView.as_view(), name="logout"),
    path("", include("melrater.core.urls")),
    # montages contain subject-derived images, so media requires login and is
    # served by Django itself (works with DEBUG off; fine at this tool's scale)
    path("media/<path:path>", views.media_file, name="media"),
]
