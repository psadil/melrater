from django.urls import path

from melrater.core import views

urlpatterns = [
    path("", views.run_list, name="run-list"),
    path(
        "runs/<int:run_id>/ic/<int:index>/",
        views.component_detail,
        name="component-detail",
    ),
    path(
        "runs/<int:run_id>/ic/<int:index>/rate/",
        views.component_rate,
        name="component-rate",
    ),
]
