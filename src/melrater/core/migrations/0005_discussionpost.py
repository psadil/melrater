import uuid

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    """The per-component discussion thread.

    No RunPython, and nothing at all happens to `Classification.note`. The
    notes already written stay exactly where they are; what changes is that
    `selectors.notes_for_component` stops handing anyone but their author back
    -- a read, not a schema fact. Copying them into threads would republish,
    under a new heading, text whose authors were told only raters who had
    already rated would see it.
    """

    dependencies = (("core", "0004_classification_note"),)

    operations = (
        migrations.CreateModel(
            name="DiscussionPost",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                (
                    "uuid",
                    models.UUIDField(default=uuid.uuid4, editable=False, unique=True),
                ),
                (
                    "label",
                    models.CharField(
                        choices=[
                            ("Signal", "Signal"),
                            ("Noise", "Noise"),
                            ("Unknown", "Unknown"),
                        ],
                        max_length=10,
                    ),
                ),
                ("body", models.TextField()),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                (
                    "edited_at",
                    models.DateTimeField(blank=True, default=None, null=True),
                ),
                (
                    "component",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="posts",
                        to="core.component",
                    ),
                ),
                (
                    "reviewer",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="posts",
                        to="core.reviewer",
                    ),
                ),
            ],
            options={
                "ordering": ["created_at", "id"],
                "indexes": [
                    models.Index(
                        fields=["component", "created_at"],
                        name="post_component_created_idx",
                    )
                ],
            },
        ),
    )
