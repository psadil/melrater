from typing import TYPE_CHECKING, ClassVar
from uuid import uuid4

from django.conf import settings
from django.db import models

if TYPE_CHECKING:
    # Stub-only (django-stubs): the type of a reverse FK accessor. Declaring
    # the accessors as plain annotations gives them types without the mypy
    # plugin; Django ignores un-assigned annotations, so no field is created.
    from django.db.models.fields.related_descriptors import RelatedManager


# Every model below carries a natural key, which is what lets `dumpdata` and
# `loaddata` move runs between databases: with --natural-primary the fixture
# holds no primary keys at all, so an imported run gets a fresh id on the target
# without colliding with anything already there, and re-importing the same
# fixture updates rather than duplicates. That is the fallback path for when the
# ingest API is unreachable; see the README.


class RunManager(models.Manager["Run"]):
    def get_by_natural_key(self, uuid: str) -> "Run":
        return self.get(uuid=uuid)


class Run(models.Model):
    """One preprocessed BOLD run's MELODIC+pyFIX derivatives directory."""

    # The row's portable identity, and the montage directory name. Deliberately
    # not the primary key (SQLite indexes an integer pk far more compactly) and
    # deliberately not `path`, which is a laptop-local absolute path that
    # `import_run --base-dir` exists to rewrite.
    uuid = models.UUIDField(default=uuid4, unique=True, editable=False)
    # Not unique: it means nothing on a server that ingests over the API, and
    # a uniqueness violation there would turn a re-pushed run whose source
    # directory moved into a 500. `ingest_run` and `selectors.run_ingested`
    # both check it explicitly with .filter(...).exists(), which is the guard
    # that actually matters and the only place it is read.
    path = models.CharField(max_length=500)
    label = models.CharField(max_length=200)
    # BIDS entities, named as the catalog names them so that querying this
    # database by hand reads the same as querying the catalog.
    sub = models.CharField(max_length=100, blank=True, default="")
    ses = models.CharField(max_length=100, blank=True, default="")
    task = models.CharField(max_length=100, blank=True, default="")
    run = models.CharField(max_length=100, blank=True, default="")
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
    # The montage set's content fingerprint (montage.digest_montages, kept to
    # montage.DIGEST_LENGTH hex characters) and the directory the files live
    # under: runs/<uuid>/<montage_digest>/. Being derived from the bytes it is
    # what makes montage URLs immutably cacheable without a revision counter —
    # a re-render writes a *different* directory, so no URL ever changes
    # meaning — and it is the same value in every database that holds the run,
    # which is what lets a push decide "already present" exactly.
    montage_digest = models.CharField(max_length=16, blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)

    objects: ClassVar[RunManager] = RunManager()

    components: "RelatedManager[Component]"

    class Meta:
        indexes: ClassVar = [models.Index(fields=["label"])]

    def natural_key(self) -> tuple[str]:
        return (str(self.uuid),)

    def __str__(self) -> str:
        return str(self.label)


class ComponentManager(models.Manager["Component"]):
    def get_by_natural_key(self, index: int, run_uuid: str) -> "Component":
        return self.get(index=index, run__uuid=run_uuid)


class Component(models.Model):
    """One independent component of a run (1-based index)."""

    run = models.ForeignKey(Run, on_delete=models.CASCADE, related_name="components")
    index = models.PositiveIntegerField(help_text="1-based IC number")
    explained_var = models.FloatField()
    total_var = models.FloatField()
    timecourse = models.JSONField()
    spectrum = models.JSONField()
    metrics = models.JSONField(help_text="Per-metric raw value and robust z-score")

    objects: ClassVar[ComponentManager] = ComponentManager()

    classifications: "RelatedManager[Classification]"

    class Meta:
        ordering: ClassVar = ["run", "index"]
        constraints: ClassVar = [
            models.UniqueConstraint(
                fields=["run", "index"], name="unique_component_per_run"
            )
        ]

    def natural_key(self) -> tuple[int, str]:
        return (int(self.index), *self.run.natural_key())

    def __str__(self) -> str:
        return f"{self.run} IC {self.index}"


class ReviewerManager(models.Manager["Reviewer"]):
    def get_by_natural_key(self, name: str) -> "Reviewer":
        return self.get(name=name)


class Reviewer(models.Model):
    """A source of classifications: a human user or a FIX model+threshold."""

    class Kind(models.TextChoices):
        HUMAN = "human", "Human"
        FIX = "fix", "FIX"

    kind = models.CharField(max_length=10, choices=Kind.choices)
    name = models.CharField(max_length=200, unique=True)
    # PROTECT, not CASCADE: a reviewer's ratings are the study's output, and
    # deleting the user row must not silently take them with it. Detach or
    # deactivate the account instead — deleting now raises ProtectedError.
    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="reviewer",
    )
    fix_model = models.CharField(max_length=100, blank=True)
    fix_threshold = models.PositiveIntegerField(null=True, blank=True)

    objects: ClassVar[ReviewerManager] = ReviewerManager()

    classifications: "RelatedManager[Classification]"

    def natural_key(self) -> tuple[str]:
        return (str(self.name),)

    def __str__(self) -> str:
        return str(self.name)


class ClassificationManager(models.Manager["Classification"]):
    def get_by_natural_key(
        self, index: int, run_uuid: str, reviewer_name: str
    ) -> "Classification":
        return self.get(
            component__index=index,
            component__run__uuid=run_uuid,
            reviewer__name=reviewer_name,
        )


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

    objects: ClassVar[ClassificationManager] = ClassificationManager()

    class Meta:
        constraints: ClassVar = [
            models.UniqueConstraint(
                fields=["component", "reviewer"], name="unique_rating_per_reviewer"
            )
        ]

    def natural_key(self) -> tuple[int, str, str]:
        return (*self.component.natural_key(), *self.reviewer.natural_key())

    def __str__(self) -> str:
        return f"{self.reviewer}: {self.component} = {self.label}"


# Django reads `natural_key.dependencies` to order dumpdata's output so that a
# referenced model is serialized before the model that references it. It is
# attached out here, through the function's own __dict__, because setting an
# attribute on a method is invisible to a type checker and this project runs ty
# with no rule overrides (contributing.md). Tuples, per RUF012.
vars(Component.natural_key)["dependencies"] = ("core.run",)
vars(Classification.natural_key)["dependencies"] = ("core.component", "core.reviewer")
