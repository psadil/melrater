from django.db import migrations, models


class Migration(migrations.Migration):
    """A reviewer's free-text note, on the rating row it explains.

    No RunPython, unlike 0002 and 0003: there an empty list read as "no
    montages" and had to be backfilled, whereas "" already means exactly "no
    note" for every row that predates this.
    """

    dependencies = (("core", "0003_run_montage_smoothings_picks"),)

    operations = (
        migrations.AddField(
            model_name="classification",
            name="note",
            field=models.TextField(blank=True, default=""),
        ),
    )
