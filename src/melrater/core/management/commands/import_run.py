"""Import MELODIC+pyFIX runs from a bidslake catalog.

Build the catalog first with the bidslake indexer CLI:

    bidslake index -i <derivatives dir> --adapter feat -o study.duckdb

then hand it to this command; every run the catalog knows is ingested, and
already-ingested runs are skipped.

A few hundred runs is roughly an hour of rendering, so a run that fails is
reported and the batch carries on — losing forty minutes of work to one
malformed fix4melview file is not a useful default. `--stop-on-error` restores
the old behaviour, and the exit status is non-zero whenever anything failed.
"""

import os
import time
import typing as t
from dataclasses import dataclass
from pathlib import Path

import typer
from django.core.management.base import CommandError
from django_typer.management import TyperCommand

from melrater.core import lake, melodic, selectors, services


@dataclass
class Failure:
    label: str
    reason: str


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
        dry_run: t.Annotated[
            bool,
            typer.Option(help="Report what would be ingested and write nothing."),
        ] = False,
        stop_on_error: t.Annotated[
            bool,
            typer.Option(help="Abort on the first failure instead of continuing."),
        ] = False,
    ) -> None:
        """Ingest every MELODIC+pyFIX run a bidslake catalog knows about."""
        lk = lake.open_catalog(catalog, base_dir=base_dir)
        discovered = lake.discover_runs(lk, sub=sub, ses=ses, task=task, run=run)
        if not discovered:
            self.stdout.write(f"no MELODIC runs in {catalog} match the filters")
            return

        total = len(discovered)
        failures: list[Failure] = []
        n_ingested = 0
        n_skipped = 0
        started = time.monotonic()

        for position, found in enumerate(discovered, start=1):
            prefix = f"[{position}/{total}]"
            if found.inputs is None:
                failures.append(Failure(found.label, "; ".join(found.problems)))
                self.stderr.write(
                    self.style.WARNING(
                        f"{prefix} skipping {found.label}: {'; '.join(found.problems)}"
                    )
                )
                continue
            if selectors.run_ingested(found.root):
                n_skipped += 1
                self.stdout.write(
                    self.style.WARNING(f"{prefix} already ingested: {found.label}")
                )
                continue
            if dry_run:
                n_ingested += 1
                self.stdout.write(f"{prefix} would ingest {found.label}")
                continue

            run_started = time.monotonic()
            try:
                ingested = services.ingest_run(
                    source=melodic.load_run(found.inputs), image_workers=workers
                )
            # broad on purpose: one bad run must not end a batch of hundreds
            except Exception as exc:
                failures.append(Failure(found.label, f"{type(exc).__name__}: {exc}"))
                self.stderr.write(
                    self.style.ERROR(f"{prefix} FAILED {found.label}: {exc}")
                )
                if stop_on_error:
                    raise
                continue

            n_ingested += 1
            elapsed = time.monotonic() - run_started
            self.stdout.write(
                self.style.SUCCESS(
                    f"{prefix} ingested {ingested.label}: "
                    f"{ingested.components.count()} components "
                    f"({elapsed:.1f} s{self._eta(started, position, total)})"
                )
            )

        self._report(
            total=total,
            n_ingested=n_ingested,
            n_skipped=n_skipped,
            failures=failures,
            dry_run=dry_run,
        )

    def _eta(self, started: float, position: int, total: int) -> str:
        remaining = total - position
        if remaining <= 0:
            return ""
        per_run = (time.monotonic() - started) / position
        return f", ~{_duration(per_run * remaining)} left"

    def _report(
        self,
        *,
        total: int,
        n_ingested: int,
        n_skipped: int,
        failures: list[Failure],
        dry_run: bool,
    ) -> None:
        verb = "would ingest" if dry_run else "ingested"
        self.stdout.write("")
        self.stdout.write(
            f"{total} run(s): {n_ingested} {verb}, {n_skipped} already present, "
            f"{len(failures)} failed"
        )
        if not failures:
            return
        for failure in failures:
            self.stderr.write(self.style.ERROR(f"  {failure.label}: {failure.reason}"))
        # non-zero exit, so this cannot pass unnoticed in a script
        raise CommandError(f"{len(failures)} of {total} run(s) could not be ingested")


def _duration(seconds: float) -> str:
    if seconds < 90:
        return f"{seconds:.0f} s"
    if seconds < 5400:
        return f"{seconds / 60:.0f} min"
    return f"{seconds / 3600:.1f} h"
