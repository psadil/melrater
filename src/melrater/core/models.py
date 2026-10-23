from typing import TYPE_CHECKING, ClassVar
from uuid import uuid4

from django.conf import settings
from django.db import models

if TYPE_CHECKING:
    # Stub-only (django-stubs): the type of a reverse FK accessor. Declaring
    # the accessors as plain annotations gives them types without the mypy
    # plugin; Django ignores un-assigned annotations, so no field is created.
    # The annotations below name this import without quoting it, which is only
    # safe because PEP 649 (3.14) made annotations lazy — nothing evaluates
    # them at runtime, so a name that exists only for the type checker is fine.
    from django.db.models.fields.related_descriptors import RelatedManager


# Every model below carries a natural key, which is what lets `dumpdata` and
# `loaddata` move runs between databases: with --natural-primary the fixture
# holds no primary keys at all, so an imported run gets a fresh id on the target
# without colliding with anything already there, and re-importing the same
# fixture updates rather than duplicates. That is the fallback path for when the
# ingest API is unreachable; see the README.


class RunManager(models.Manager["Run"]):
    def get_by_natural_key(self, uuid: str) -> Run:
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
    # Which backgrounds this run's montages were rendered over, in display
    # order, drawn from montage.BACKGROUNDS. Always holds "func"; "anat" only
    # when the run's FEAT registration was there at render time — which is a
    # property of the *render*, not of the run, so `rerender_montages` rewrites
    # it alongside the digest. A list rather than a boolean so the montage
    # count arithmetic reads `len(...)` and a third background needs no column.
    montage_backgrounds = models.JSONField(
        default=list, help_text="Montage backgrounds rendered for this run"
    )
    # Which smoothing levels the overlay was rendered at, in display order,
    # drawn from montage.SMOOTHINGS. Every render since the vocabulary existed
    # writes both; the list is here, rather than assumed, so the montage count
    # arithmetic and the page's switcher read what this run actually has —
    # a run rendered before the smoothed variant declares `["raw"]`.
    montage_smoothings = models.JSONField(
        default=list, help_text="Overlay smoothing levels rendered for this run"
    )
    # The slice indices each axis' lightbox shows, in lightbox order
    # (`{"axial": [3, 7, ...], ...}`). The montage image is pixels only; the
    # page draws these over it as labels, positioned from their ordinal and
    # montage.LIGHTBOX_COLS. A property of the render, like the two lists above.
    montage_picks = models.JSONField(
        default=dict, help_text="Slice indices shown per axis, in lightbox order"
    )
    created_at = models.DateTimeField(auto_now_add=True)

    objects: ClassVar[RunManager] = RunManager()

    components: RelatedManager[Component]

    class Meta:
        indexes: ClassVar = [models.Index(fields=["label"])]

    def natural_key(self) -> tuple[str]:
        return (str(self.uuid),)

    def __str__(self) -> str:
        return str(self.label)


class ComponentManager(models.Manager["Component"]):
    def get_by_natural_key(self, index: int, run_uuid: str) -> Component:
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
    # How much of this timecourse ordinary least squares on the Friston-24
    # motion expansion accounts for (melodic.motion_r2), shown on the FD
    # chart. Nullable because it cannot be backfilled: the motion parameters
    # it is computed from live beside the niftis, and a server holds neither.
    # A run ingested before this existed keeps None, and the chart says
    # nothing rather than saying zero.
    motion_r2 = models.FloatField(
        null=True, blank=True, help_text="R^2 of this timecourse on Friston-24 motion"
    )

    objects: ClassVar[ComponentManager] = ComponentManager()

    classifications: RelatedManager[Classification]
    posts: RelatedManager[DiscussionPost]

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


class MetricCohortManager(models.Manager["MetricCohort"]):
    def get_by_natural_key(self, key: str) -> MetricCohort:
        return self.get(key=key)


class MetricCohort(models.Model):
    """The distributions every metric strip on the rating screen is read against.

    One scan's components are thin evidence for what an unusual value looks
    like, so a strip is drawn against every component of every scan this
    database holds. That pooling is what this row is: `refresh_metric_cohort`
    rebuilds it from the stored metrics, and `selectors.metric_cohort` reads
    it once per page.

    Stored rather than computed per request because the pass that builds it
    reads every component in the database — minutes of work at campaign
    scale, which cannot happen behind a page load. Derived rather than
    authored, so losing it costs nothing but the command: until one exists,
    every strip falls back to the scan it belongs to.
    """

    #: There is one cohort. Keeping that as a unique column rather than a
    #: convention is what makes `update_or_create` idempotent, and gives the
    #: row the natural key a fixture needs.
    KEY = "current"

    key = models.CharField(max_length=20, unique=True, default=KEY, editable=False)
    n_runs = models.PositiveIntegerField()
    n_components = models.PositiveIntegerField()
    norms = models.JSONField(
        help_text="Per-metric cohort centre, scale and percentile bands"
    )
    # The same pooling for FIX's reasons: per model, how far each feature
    # moved its log-odds across every component it explained. What the
    # verdict card's push strips are read against, so a push FIX gives
    # nearly every component in the study is visibly one.
    fix_norms = models.JSONField(
        default=dict,
        help_text="Per-model, per-feature percentile bands of FIX's contributions",
    )
    computed_at = models.DateTimeField(auto_now=True)

    objects: ClassVar[MetricCohortManager] = MetricCohortManager()

    def natural_key(self) -> tuple[str]:
        return (str(self.key),)

    def __str__(self) -> str:
        return f"{len(self.norms)} metrics over {self.n_components} components"


class ReviewerManager(models.Manager["Reviewer"]):
    def get_by_natural_key(self, name: str) -> Reviewer:
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

    classifications: RelatedManager[Classification]
    posts: RelatedManager[DiscussionPost]

    def natural_key(self) -> tuple[str]:
        return (str(self.name),)

    def __str__(self) -> str:
        return str(self.name)


class ClassificationManager(models.Manager["Classification"]):
    def get_by_natural_key(
        self, index: int, run_uuid: str, reviewer_name: str
    ) -> Classification:
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
    # Free text, private to its author: nobody else ever reads it, now or
    # later. The shared channel is DiscussionPost, which opens to a reviewer
    # once they have rated. On the row rather than in a table of its own
    # because the study's output is queried by hand out of the SQLite file,
    # and a note is worth little without the label beside it. Length is
    # bounded in services, not here: a TextField has no cap in SQLite, and the
    # limit is a policy about what a rating note is for rather than a storage
    # fact.
    note = models.TextField(blank=True, default="")
    probability = models.FloatField(
        null=True, blank=True, help_text="FIX P(signal), when the reviewer is FIX"
    )
    # Why FIX made this call (fixmodel): its log-odds before any feature, and
    # each feature's exact TreeSHAP contribution on top of that, which together
    # add up to the log-odds of `probability`. Beside the probability, for the
    # reason `note` is beside the label. Null on every human row, and on a FIX
    # row whose model was not available to explain it.
    baseline = models.FloatField(
        null=True,
        blank=True,
        help_text="FIX's log-odds before any feature (its training average)",
    )
    contributions = models.JSONField(
        null=True,
        blank=True,
        help_text="FIX's log-odds contribution per feature, when the reviewer is FIX",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    objects: ClassVar[ClassificationManager] = ClassificationManager()

    # Stub-only, like the reverse accessors above: django-stubs resolves a
    # ForeignKey's descriptor but not the implicit `<name>_id` column beside
    # it, and Django ignores an un-assigned annotation, so this creates no
    # field. Named here because both the discussion gate and its ownership
    # check compare ids rather than fetching the row.
    reviewer_id: int

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


class DiscussionPostManager(models.Manager["DiscussionPost"]):
    def get_by_natural_key(self, uuid: str) -> DiscussionPost:
        return self.get(uuid=uuid)


class DiscussionPost(models.Model):
    """One human reviewer's message in a component's discussion thread.

    The counterpart to `Classification.note`: a note is its author's alone,
    this is what reviewers say to each other. Reading and writing both open
    only once the reviewer has rated the component
    (`selectors.discussion_for_component` is the gate).
    """

    # The row's portable identity, as `Run.uuid` is: it names the same post in
    # every copy of this database, which is what lets the edit and delete URLs
    # carry it, and (component, reviewer) could not be a natural key because a
    # reviewer posts many times.
    uuid = models.UUIDField(default=uuid4, unique=True, editable=False)
    component = models.ForeignKey(
        Component, on_delete=models.CASCADE, related_name="posts"
    )
    # Reviewer rather than the user directly: `Reviewer.name` is the identity
    # the page prints, and the gate then joins Classification on reviewer_id
    # with no auth_user join. Reviewer.user is PROTECT, so deleting an account
    # still cannot silently take reviewer work with it.
    reviewer = models.ForeignKey(
        Reviewer, on_delete=models.CASCADE, related_name="posts"
    )
    # The author's label *at the moment they wrote this*, copied rather than
    # joined. `rate_component` is an update_or_create, so a reviewer may change
    # their mind at any time; a live join would rewrite the verdict shown
    # beside every message they ever wrote about this component, and a post
    # arguing for noise would end up captioned SIGNAL.
    label = models.CharField(max_length=10, choices=Classification.Label.choices)
    body = models.TextField()
    created_at = models.DateTimeField(auto_now_add=True)
    # Set explicitly in services, not auto_now: auto_now fires for any save
    # that names the field and so cannot tell an edit from a touch. None means
    # never edited, which is what the page renders on.
    edited_at = models.DateTimeField(null=True, blank=True, default=None)

    objects: ClassVar[DiscussionPostManager] = DiscussionPostManager()

    #: See Classification.reviewer_id.
    reviewer_id: int

    class Meta:
        # Oldest first: a thread is read in the order it was written. `id`
        # breaks the tie for two posts sharing a timestamp.
        ordering: ClassVar = ["created_at", "id"]
        indexes: ClassVar = [
            models.Index(
                fields=["component", "created_at"], name="post_component_created_idx"
            )
        ]

    def natural_key(self) -> tuple[str]:
        return (str(self.uuid),)

    def __str__(self) -> str:
        return f"{self.reviewer} on {self.component}"


# Django reads `natural_key.dependencies` to order dumpdata's output so that a
# referenced model is serialized before the model that references it. It is
# attached out here, through the function's own __dict__, because setting an
# attribute on a method is invisible to a type checker and this project runs ty
# with no rule overrides (contributing.md). Tuples, per RUF012.
vars(Component.natural_key)["dependencies"] = ("core.run",)
vars(Classification.natural_key)["dependencies"] = ("core.component", "core.reviewer")
vars(DiscussionPost.natural_key)["dependencies"] = ("core.component", "core.reviewer")
