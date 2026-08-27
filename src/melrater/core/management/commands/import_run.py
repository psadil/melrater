"""Import MELODIC+pyFIX runs from a bidslake catalog.

Build the catalog first with the bidslake indexer CLI:

    bidslake index -i <derivatives dir> --adapter feat -o study.duckdb

then hand it to this command; every run the catalog knows is ingested,
already-ingested and incomplete runs are reported and skipped.
"""

import os
import typing as t
from pathlib import Path

import typer
from django_typer.management import TyperCommand

from melrater.core import lake, melodic, selectors, services


class Command(TyperCommand):
    def handle(
        self,
        catalog: t.Annotated[
            Path,
            typer.Argument(
                exists=True,
                file_okay=True,
                dir_okay=False,
                readable=True,
                help="A bidslake .duckdb catalog (build with `bidslake index --adapter feat`).",
            ),
        ],
        base_dir: t.Annotated[
            Path | None,
            typer.Option(
                help="Rebase the catalog's dataset roots under this directory "
                "(for data that moved since it was indexed)."
            ),
        ] = None,
        sub: t.Annotated[
            str | None, typer.Option(help="Only this subject label.")
        ] = None,
        ses: t.Annotated[
            str | None, typer.Option(help="Only this session label.")
        ] = None,
        task: t.Annotated[str | None, typer.Option(help="Only this task.")] = None,
        run: t.Annotated[str | None, typer.Option(help="Only this run label.")] = None,
        workers: t.Annotated[
            int,
            typer.Option(
                help="Parallel processes for montage rendering (default: cpus-2)."
            ),
        ] = max(1, (os.cpu_count() or 2) - 2),
    ) -> None:
        """Ingest every MELODIC+pyFIX run a bidslake catalog knows about."""
        lk = lake.open_catalog(catalog, base_dir=base_dir)
        discovered = lake.discover_runs(lk, sub=sub, ses=ses, task=task, run=run)
        if not discovered:
            self.stdout.write(f"no MELODIC runs in {catalog} match the filters")
            return
        for found in discovered:
            if found.inputs is None:
                self.stderr.write(
                    self.style.WARNING(
                        f"skipping {found.label}: {'; '.join(found.problems)}"
                    )
                )
                continue
            if selectors.run_ingested(found.root):
                self.stdout.write(
                    self.style.WARNING(f"already ingested: {found.label}")
                )
                continue
            ingested = services.ingest_run(
                source=melodic.load_run(found.inputs), image_workers=workers
            )
            n = ingested.components.count()
            self.stdout.write(
                self.style.SUCCESS(f"ingested {ingested.label}: {n} components")
            )
