"""Export ingested runs as Django fixtures plus their montages.

The far side is stock Django: `loaddata` on each `runs-NNN.json` and `tar -x`
for each `runs-NNN-media.tar`. Every model here carries a natural key, so the
fixtures hold no primary keys — an imported run is inserted with a fresh id
rather than overwriting whatever already occupies that id, and re-loading the
same fixture updates in place instead of duplicating.

Two things `dumpdata` cannot express are the reason this command exists:

* a *subset* of runs, so run 301 can be shipped to a server that already has
  1-300 and is the authoritative copy of everybody's ratings; and
* the exclusion of **human** reviewers and their classifications, which makes
  it structurally impossible for a fixture to overwrite work done on the
  server. Only FIX verdicts travel.

See deploy.md for the operational sequence.
"""

import tarfile
import typing as t
from pathlib import Path

import typer
from django.core import serializers
from django_typer.management import TyperCommand

from melrater.core import storage as montage_store
from melrater.core.models import Classification, Component, Reviewer, Run

#: `loaddata` reads a whole fixture into memory inside one transaction, and a
#: run's components carry their timecourses and spectra as JSON — a few hundred
#: runs in one file is comfortably more than the container's memory limit.
DEFAULT_RUNS_PER_BUNDLE = 25


class Command(TyperCommand):
    def handle(
        self,
        run_ids: t.Annotated[
            list[int] | None,
            typer.Argument(help="Run ids to export (default: every run)."),
        ] = None,
        out: t.Annotated[
            Path, typer.Option(help="Directory to write the bundle files into.")
        ] = Path("outgoing"),
        runs_per_bundle: t.Annotated[
            int, typer.Option(help="Runs per fixture/tar pair.")
        ] = DEFAULT_RUNS_PER_BUNDLE,
    ) -> None:
        """Write run fixtures and montage tars for transfer to the server."""
        if runs_per_bundle < 1:
            raise typer.BadParameter("--runs-per-bundle must be at least 1")
        runs = list(
            Run.objects.filter(pk__in=run_ids).order_by("label")
            if run_ids
            else Run.objects.all().order_by("label")
        )
        if run_ids and len(runs) != len(set(run_ids)):
            missing = set(run_ids) - {run.pk for run in runs}
            raise typer.BadParameter(f"unknown run ids: {sorted(missing)}")
        if not runs:
            self.stdout.write("no runs to export")
            return

        out.mkdir(parents=True, exist_ok=True)
        chunks = [
            runs[i : i + runs_per_bundle] for i in range(0, len(runs), runs_per_bundle)
        ]
        for number, chunk in enumerate(chunks, start=1):
            fixture = out / f"runs-{number:03d}.json"
            archive = out / f"runs-{number:03d}-media.tar"
            n_files = _write_bundle(chunk, fixture, archive)
            self.stdout.write(
                self.style.SUCCESS(
                    f"[{number}/{len(chunks)}] {fixture.name}: {len(chunk)} run(s), "
                    f"{archive.name}: {n_files} montage(s)"
                )
            )
        self.stdout.write("")
        self.stdout.write(f"{len(runs)} run(s) in {len(chunks)} bundle(s) under {out}")
        self.stdout.write("Load them on the server with `loaddata`; see deploy.md.")


def _write_bundle(runs: list[Run], fixture: Path, archive: Path) -> int:
    """One chunk: its fixture and its montages. Returns the file count."""
    components = Component.objects.filter(run__in=runs).order_by("run_id", "index")
    # FIX reviewers only. A human reviewer would drag their ratings along, and
    # loading those onto the server would overwrite the very work this exists
    # to preserve.
    fix_reviewers = (
        Reviewer.objects.filter(
            kind=Reviewer.Kind.FIX, classifications__component__run__in=runs
        )
        .distinct()
        .order_by("name")
    )
    classifications = Classification.objects.filter(
        component__run__in=runs, reviewer__kind=Reviewer.Kind.FIX
    ).order_by("component_id", "reviewer_id")

    with fixture.open("w", encoding="utf-8") as handle:
        serializers.serialize(
            "json",
            [*fix_reviewers, *runs, *components, *classifications],
            stream=handle,
            indent=1,
            use_natural_foreign_keys=True,
            use_natural_primary_keys=True,
        )

    storage = montage_store.montage_storage()
    n_files = 0
    with tarfile.open(archive, "w") as tar:
        for run in runs:
            for name in montage_store.names_for_run(run.uuid):
                with storage.open(name) as montage_file:
                    info = tarfile.TarInfo(name)
                    info.size = storage.size(name)
                    tar.addfile(info, montage_file)
                n_files += 1
    return n_files
