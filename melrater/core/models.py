from typing import ClassVar

from django.conf import settings
from django.db import models


class Run(models.Model):
    """One preprocessed BOLD run's MELODIC+pyFIX derivatives directory."""

    path = models.CharField(max_length=500, unique=True)
    label = models.CharField(max_length=200)
    tr = models.FloatField(help_text="Repetition time (s)")
    n_timepoints = models.PositiveIntegerField()
    fd = models.JSONField(help_text="Framewise displacement per volume (mm)")
    frequencies = models.JSONField(help_text="Power-spectrum frequency axis (Hz)")
    metric_stats = models.JSONField(
        help_text="Per-metric distribution stats across this run's components"
    )
    montage_format = models.CharField(
        max_length=8, default="avif", help_text="File extension of rendered montages"
    )
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self) -> str:
        return str(self.label)


class Component(models.Model):
    """One independent component of a run (1-based index)."""

    run = models.ForeignKey(Run, on_delete=models.CASCADE, related_name="components")
    index = models.PositiveIntegerField(help_text="1-based IC number")
    explained_var = models.FloatField()
    total_var = models.FloatField()
    timecourse = models.JSONField()
    spectrum = models.JSONField()
    metrics = models.JSONField(help_text="Per-metric raw value and robust z-score")

    class Meta:
        ordering: ClassVar = ["run", "index"]
        constraints: ClassVar = [
            models.UniqueConstraint(
                fields=["run", "index"], name="unique_component_per_run"
            )
        ]

    def __str__(self) -> str:
        return f"{self.run} IC {self.index}"


class Reviewer(models.Model):
    """A source of classifications: a human user or a FIX model+threshold."""

    class Kind(models.TextChoices):
        HUMAN = "human", "Human"
        FIX = "fix", "FIX"

    kind = models.CharField(max_length=10, choices=Kind.choices)
    name = models.CharField(max_length=200, unique=True)
    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.CASCADE,
        related_name="reviewer",
    )
    fix_model = models.CharField(max_length=100, blank=True)
    fix_threshold = models.PositiveIntegerField(null=True, blank=True)

    def __str__(self) -> str:
        return str(self.name)


class Classification(models.Model):
    """A reviewer's current label for one component."""

    class Label(models.TextChoices):
        SIGNAL = "Signal", "Signal"
        NOISE = "Noise", "Noise"
        UNKNOWN = "Unknown", "Unknown"

    component = models.ForeignKey(
        Component, on_delete=models.CASCADE, related_name="classifications"
    )
    reviewer = models.ForeignKey(
        Reviewer, on_delete=models.CASCADE, related_name="classifications"
    )
    label = models.CharField(max_length=10, choices=Label.choices)
    probability = models.FloatField(
        null=True, blank=True, help_text="FIX P(signal), when the reviewer is FIX"
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints: ClassVar = [
            models.UniqueConstraint(
                fields=["component", "reviewer"], name="unique_rating_per_reviewer"
            )
        ]

    def __str__(self) -> str:
        return f"{self.reviewer}: {self.component} = {self.label}"
