"""Keep every classification: a row per save instead of a row per reviewer.

Until now a re-rating overwrote the reviewer's row (`update_or_create`), so
the call they made before FIX's verdict and the discussion opened to them was
gone the moment they changed their mind. From here on every save adds a row,
and a reviewer's rating as it stands is their newest row for the component --
`Classification.objects` in the app, the `core_classification_current` view
created at the end of this migration for SQL written by hand.

What the rows already here become: each one is a single save. One never
written again after it was created (`updated_at` within a second of
`created_at`) is exactly what the new code would have recorded, so it stays a
plain rating. A human row that was written again -- a re-rating, the same
label clicked twice, or a note saved -- has lost whatever it said before, and
nothing left can tell those apart; it is marked `revised` and timed at its
last write, the moment the state it holds was saved. FIX never re-rates, so
every FIX verdict stays a plain rating.

Order matters here, and so does cost:

- The view comes last. Django rebuilds a SQLite table by creating a copy,
  dropping the original and renaming the copy into place, and with
  `legacy_alter_table = OFF` (which Django sets on every connection) that
  rename fails while any view reads the table. Steps 1, 4 and 6 below are
  such rebuilds. Any later migration that rebuilds core_classification has to
  drop the view first and create it again after: copy the SQL from here.
- Every rebuild copies the whole table, FIX's contributions JSON included,
  and the uuid fill and the column drop rewrite every row too -- about five
  passes over it, while the deployment is down. Time it against a snapshot
  before deploying (README).
- The uuid arrives nullable and becomes unique only once filled: adding a
  unique column with a default has the rebuild call `uuid4()` once and give
  every row that same value.

Reversing works until some reviewer has two rows for one component. After
that, putting the one-row-per-reviewer constraint back fails inside this
migration's transaction and nothing changes -- a rollback must not quietly
throw history away.

Spelled out rather than imported, as 0007 and 0009 are: a migration has to keep
meaning what it means today after the modules have moved on.
"""

import uuid

import django.db.models.manager
from django.db import migrations, models

#: A row written again more than this long after it was created was revised.
#: Creating one stamps `created_at` and `updated_at` microseconds apart, while
#: a second save takes another request.
REVISED_AFTER_SECONDS = 1.0

#: Rows per uuid UPDATE: the pks are read up front and the JSON never is,
#: which is what keeps this inside the server container's memory limit.
BATCH = 1000

CREATE_VIEW = """
CREATE VIEW core_classification_current AS
SELECT c.*
FROM core_classification AS c
WHERE NOT EXISTS (
    SELECT 1
    FROM core_classification AS newer
    WHERE newer.component_id = c.component_id
      AND newer.reviewer_id = c.reviewer_id
      AND newer.id > c.id
)
"""

DROP_VIEW = "DROP VIEW core_classification_current"


def keep_history(apps, schema_editor) -> None:
    Classification = apps.get_model("core", "Classification")
    pks = list(Classification.objects.values_list("pk", flat=True))
    for start in range(0, len(pks), BATCH):
        Classification.objects.bulk_update(
            [
                Classification(pk=pk, uuid=uuid.uuid4())
                for pk in pks[start : start + BATCH]
            ],
            ("uuid",),
        )

    human = list(
        Classification.objects.filter(reviewer__kind="human").values_list(
            "pk", "created_at", "updated_at"
        )
    )
    revised = [
        pk
        for pk, created_at, updated_at in human
        if (updated_at - created_at).total_seconds() >= REVISED_AFTER_SECONDS
    ]
    for start in range(0, len(revised), BATCH):
        # QuerySet.update rather than save(): created_at is auto_now_add, which
        # replaces whatever value a save hands it
        Classification.objects.filter(pk__in=revised[start : start + BATCH]).update(
            action="revised", created_at=models.F("updated_at")
        )
    if human:
        print(
            f"0010: kept {len(human)} human ratings as history, marking "
            f"{len(revised)} revised (overwritten before, earlier calls lost)"
        )


class Migration(migrations.Migration):
    dependencies = (("core", "0009_fill_fix_contributions"),)

    operations = (
        # 1. a table rebuild
        migrations.RemoveConstraint(
            model_name="classification",
            name="unique_rating_per_reviewer",
        ),
        # 2. the index the constraint used to provide, which the current-rows
        # rule now probes once per row it keeps
        migrations.AddIndex(
            model_name="classification",
            index=models.Index(
                fields=["component", "reviewer"], name="classification_pair_idx"
            ),
        ),
        # 3. nullable and default-less, so a plain ADD COLUMN
        migrations.AddField(
            model_name="classification",
            name="uuid",
            field=models.UUIDField(editable=False, null=True),
        ),
        # 4. a table rebuild: NOT NULL
        migrations.AddField(
            model_name="classification",
            name="action",
            field=models.CharField(
                choices=[
                    ("rate", "Rated"),
                    ("note", "Edited the note"),
                    ("revised", "Revised before history was kept"),
                ],
                default="rate",
                help_text=(
                    "What added this row; 'revised' marks a rating already "
                    "overwritten when history began"
                ),
                max_length=8,
            ),
        ),
        # 5. before the managers change below, so `objects` is still a plain
        # manager over every row here
        migrations.RunPython(keep_history, migrations.RunPython.noop),
        # 6. a table rebuild
        migrations.AlterField(
            model_name="classification",
            name="uuid",
            field=models.UUIDField(default=uuid.uuid4, editable=False, unique=True),
        ),
        # 7. SQLite's DROP COLUMN, which rewrites every row
        migrations.RemoveField(
            model_name="classification",
            name="updated_at",
        ),
        # 8, 9. state only
        migrations.AlterModelOptions(
            name="classification",
            options={"default_manager_name": "history"},
        ),
        migrations.AlterModelManagers(
            name="classification",
            managers=[("history", django.db.models.manager.Manager())],
        ),
        # 10. last: see the module docstring
        migrations.RunSQL(CREATE_VIEW, reverse_sql=DROP_VIEW),
    )
