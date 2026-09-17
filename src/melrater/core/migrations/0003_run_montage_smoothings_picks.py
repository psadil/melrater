from django.db import migrations, models


def set_raw_smoothing(apps, schema_editor):
    """Every run rendered before this had exactly the unsmoothed overlay.

    Not left to the field default, for the reason 0002 gives: an empty list
    reads as "no smoothing levels" and would render a component page with no
    images at all. Their files are still named the old way either way — the
    montage filenames gained a smoothing token in the same change, so an
    existing run needs a re-render and a re-push regardless. The slice picks
    stay empty until that re-render, which only costs the labels.
    """
    apps.get_model("core", "Run").objects.update(montage_smoothings=["raw"])


class Migration(migrations.Migration):
    dependencies = (("core", "0002_run_montage_backgrounds"),)

    operations = (
        migrations.AddField(
            model_name="run",
            name="montage_smoothings",
            field=models.JSONField(
                default=list,
                help_text="Overlay smoothing levels rendered for this run",
            ),
        ),
        migrations.AddField(
            model_name="run",
            name="montage_picks",
            field=models.JSONField(
                default=dict,
                help_text="Slice indices shown per axis, in lightbox order",
            ),
        ),
        migrations.RunPython(
            set_raw_smoothing, migrations.RunPython.noop, elidable=True
        ),
    )
