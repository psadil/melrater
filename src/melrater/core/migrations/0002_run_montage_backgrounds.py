from django.db import migrations, models


def set_functional_background(apps, schema_editor):
    """Every run rendered before this had exactly the functional background.

    Not left to the field default: an empty list reads as "no backgrounds" and
    would render a component page with no images at all. Their files are still
    named the old way either way — the montage filenames gained a background
    token in the same change, so an existing run needs a re-render and a
    re-push regardless. This buys a page that renders and an honest 404.
    """
    apps.get_model("core", "Run").objects.update(montage_backgrounds=["func"])


class Migration(migrations.Migration):
    dependencies = (("core", "0001_initial"),)

    operations = (
        migrations.AddField(
            model_name="run",
            name="montage_backgrounds",
            field=models.JSONField(
                default=list, help_text="Montage backgrounds rendered for this run"
            ),
        ),
        migrations.RunPython(
            set_functional_background, migrations.RunPython.noop, elidable=True
        ),
    )
