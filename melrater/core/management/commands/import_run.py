import os
from pathlib import Path
from typing import cast

from django.core.management.base import BaseCommand, CommandError, CommandParser

from melrater.core.services import RunAlreadyIngested, ingest_run


class Command(BaseCommand):
    help = "Ingest one or more MELODIC+pyFIX derivatives directories"

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument("paths", nargs="+", type=Path)
        parser.add_argument(
            "--workers",
            type=int,
            default=max(1, (os.cpu_count() or 2) - 2),
            help="Parallel processes for montage rendering (default: cpus-2)",
        )

    def handle(self, *args: object, **options: object) -> None:
        paths = cast("list[Path]", options["paths"])
        workers = cast("int", options["workers"])
        for path in paths:
            if not path.is_dir():
                raise CommandError(f"not a directory: {path}")
            try:
                run = ingest_run(path=path, image_workers=workers)
            except RunAlreadyIngested:
                self.stdout.write(self.style.WARNING(f"already ingested: {path}"))
                continue
            n = run.components.count()
            self.stdout.write(
                self.style.SUCCESS(f"ingested {run.label}: {n} components")
            )
