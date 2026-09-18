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
    path(
        "runs/<int:run_id>/ic/<int:index>/note/",
        views.component_note,
        name="component-note",
    ),
    path("prefs/axis/", views.set_axis, name="set-axis"),
    path("prefs/background/", views.set_background, name="set-background"),
    path("prefs/smoothing/", views.set_smoothing, name="set-smoothing"),
]
