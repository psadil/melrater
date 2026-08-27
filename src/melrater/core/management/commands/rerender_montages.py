import os
from typing import cast

from django.core.management.base import BaseCommand, CommandError, CommandParser

from melrater.core.models import Run
from melrater.core.services import rerender_montages


class Command(BaseCommand):
    help = "Re-render slice montages for ingested runs (after display changes)"

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument(
            "run_ids", nargs="*", type=int, help="Run ids (default: every run)"
        )
        parser.add_argument(
            "--workers",
            type=int,
            default=max(1, (os.cpu_count() or 2) - 2),
            help="Parallel processes for montage rendering (default: cpus-2)",
        )

    def handle(self, *args: object, **options: object) -> None:
        run_ids = cast("list[int]", options["run_ids"])
        workers = cast("int", options["workers"])
        runs = Run.objects.filter(pk__in=run_ids) if run_ids else Run.objects.all()
        if run_ids and runs.count() != len(set(run_ids)):
            missing = set(run_ids) - {run.pk for run in runs}
            raise CommandError(f"unknown run ids: {sorted(missing)}")
        for run in runs:
            rerender_montages(run=run, image_workers=workers)
            self.stdout.write(self.style.SUCCESS(f"re-rendered {run.label}"))
