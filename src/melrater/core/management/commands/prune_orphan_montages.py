"""Delete montage directories no run row names.

Two things leave these behind, both by design. A push writes its montages
before the row that names them, so one that dies partway leaves a whole
directory unreachable rather than leaving a run with broken images. And a
re-render writes its new set beside the old one, so an interruption between
those two steps leaves the superseded set on disk.

Neither is visible to anybody — that is the point — which is also why nothing
reclaims the space on its own.
"""

import typing as t

import typer
from django_typer.management import TyperCommand

from melrater.core import storage as montage_store
from melrater.core.models import Run


class Command(TyperCommand):
    def handle(
        self,
        dry_run: t.Annotated[
            bool, typer.Option(help="Report what would be deleted and delete nothing.")
        ] = False,
    ) -> None:
        """Reclaim montage directories that no run refers to."""
        current = {
            str(uuid): str(digest)
            for uuid, digest in Run.objects.values_list("uuid", "montage_digest")
        }
        n_runs = 0
        n_sets = 0
        for run_uuid in sorted(montage_store.stored_run_uuids()):
            keep = current.get(run_uuid)
            if keep is None:
                n_runs += 1
                self.stdout.write(f"orphan run {run_uuid}")
                if not dry_run:
                    montage_store.delete_run(run_uuid)
                continue
            for digest in montage_store.digests_for_run(run_uuid):
                if digest != keep:
                    n_sets += 1
                    self.stdout.write(f"superseded {run_uuid}/{digest}")
                    if not dry_run:
                        montage_store.delete_digest(run_uuid, digest)

        verb = "would remove" if dry_run else "removed"
        self.stdout.write("")
        self.stdout.write(
            f"{verb} {n_runs} orphan run(s) and {n_sets} superseded montage set(s)"
        )
