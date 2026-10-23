"""Rebuild the cohort every metric strip on the rating screen is read against.

A pyFIX feature is shown as a robust z-score, and the reference for it is
every component of every run this database holds. That reference changes each
time runs are ingested, and reading a few hundred thousand components is far
too much to do behind a page load — so it is computed here, once, and stored.

Run it after a batch of ingests (or pushes: the server has its own copy of the
runs, and therefore its own cohort). Until it has run at all, every strip
falls back to comparing a component only with the others in its own scan,
which is what melrater did before cohorts existed.

It pools FIX's reasons the same way: for each model, how far each feature
moved its log-odds across every component it explained, which is what the
verdict card's push strips are read against.

Cheap to re-run and safe to interrupt: nothing else writes the row, and the
previous cohort stays in place until a new one is complete.
"""

import typing as t

import typer
from django_typer.management import TyperCommand

from melrater.core import metrics, selectors, services


class Command(TyperCommand):
    def handle(
        self,
        chunk_size: t.Annotated[
            int,
            typer.Option(help="Components to fetch per database round-trip.", min=1),
        ] = 2000,
        dry_run: t.Annotated[
            bool,
            typer.Option(help="Report what would be stored and write nothing."),
        ] = False,
    ) -> None:
        """Recompute the metric distributions from every ingested component."""
        sweep = selectors.metric_raw_values(chunk_size=chunk_size)
        if not sweep.n_components:
            self.stdout.write("no components to pool — ingest some runs first")
            return
        norms = metrics.build_metric_norms(sweep.values)
        # A metric with no spread over the whole cohort has no scale to state
        # a position in, so it is left out and its rows keep the scan frame.
        constant = sorted(set(sweep.values) - set(norms))
        for name in constant:
            self.stdout.write(f"constant across the cohort, not pooled: {name}")

        pushes = selectors.fix_contribution_values(chunk_size=chunk_size)
        fix_norms = metrics.build_push_norms(pushes.values)

        summary = (
            f"{len(norms)} metrics over {sweep.n_components} components "
            f"in {sweep.n_runs} runs; FIX's reasons from {len(fix_norms)} "
            f"model(s) over {pushes.n_components} components"
        )
        if dry_run:
            self.stdout.write(f"would store {summary}")
            return
        services.store_metric_cohort(
            norms=norms,
            n_runs=sweep.n_runs,
            n_components=sweep.n_components,
            fix_norms=fix_norms,
        )
        self.stdout.write(f"stored {summary}")
