from django.contrib import admin

from melrater.core.models import Classification, Component, Reviewer, Run


@admin.register(Run)
class RunAdmin(admin.ModelAdmin):
    list_display = ("label", "tr", "n_timepoints", "created_at")


@admin.register(Component)
class ComponentAdmin(admin.ModelAdmin):
    list_display = ("run", "index", "explained_var")
    list_filter = ("run",)


@admin.register(Reviewer)
class ReviewerAdmin(admin.ModelAdmin):
    list_display = ("name", "kind", "user", "fix_model", "fix_threshold")
    list_filter = ("kind",)


@admin.register(Classification)
class ClassificationAdmin(admin.ModelAdmin):
    list_display = ("component", "reviewer", "label", "probability", "updated_at")
    list_filter = ("label", "reviewer")
