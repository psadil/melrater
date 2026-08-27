import os
import typing as t

import typer
from django_typer.management import TyperCommand

from melrater.core.models import Run
from melrater.core.services import rerender_montages


class Command(TyperCommand):
    def handle(
        self,
        run_ids: t.Annotated[
            list[int] | None,
            typer.Argument(help="Run ids (default: every run)."),
        ] = None,
        workers: t.Annotated[
            int,
            typer.Option(
                help="Parallel processes for montage rendering (default: cpus-2)."
            ),
        ] = max(1, (os.cpu_count() or 2) - 2),
    ) -> None:
        """Re-render slice montages for ingested runs (after display changes)."""
        runs = Run.objects.filter(pk__in=run_ids) if run_ids else Run.objects.all()
        if run_ids and runs.count() != len(set(run_ids)):
            missing = set(run_ids) - {run.pk for run in runs}
            raise typer.BadParameter(f"unknown run ids: {sorted(missing)}")
        for run in runs:
            rerender_montages(run=run, image_workers=workers)
            self.stdout.write(self.style.SUCCESS(f"re-rendered {run.label}"))
