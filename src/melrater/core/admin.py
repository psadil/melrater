from django.contrib import admin

from melrater.core.models import (
    Classification,
    Component,
    DiscussionPost,
    Reviewer,
    Run,
)


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


@admin.register(DiscussionPost)
class DiscussionPostAdmin(admin.ModelAdmin):
    list_display = ("component", "reviewer", "label", "created_at", "edited_at")
    list_filter = ("reviewer",)
    # Load-bearing: Component.__str__ is f"{self.run} IC {self.index}", so the
    # changelist is an N+1 across two tables without it.
    list_select_related = ("reviewer", "component", "component__run")
    readonly_fields = ("uuid", "created_at")
