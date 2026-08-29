"""Send ingested runs to a running melrater deployment over its ingest API.

This is the whole transfer: no fixture to write, no tar to rsync, nothing to
extract or chown on the far side, and no `loaddata` to run there. One run is
one request, so a batch that dies partway has simply sent fewer runs.

Runs the server already holds with the same montages are skipped, which makes
re-running this after an interruption free. That comparison is exact rather
than approximate because `Run.montage_digest` is derived from the montage
bytes: both databases compute the same value without either being told.

The password is never taken as an argument. Give `--password-env` the *name*
of an environment variable holding it, or let the command prompt.
"""

import os
import typing as t

import httpx
import typer
from django.core.management.base import CommandError
from django_typer.management import TyperCommand

from melrater.core import push, selectors
from melrater.core.models import Run


class Command(TyperCommand):
    def handle(
        self,
        run_ids: t.Annotated[
            list[int] | None,
            typer.Argument(help="Run ids to push (default: every run)."),
        ] = None,
        server: t.Annotated[
            str,
            typer.Option(help="Base URL of the deployment, e.g. https://1.2.3.4"),
        ] = os.environ.get("MELRATER_PUSH_URL", ""),
        user: t.Annotated[
            str, typer.Option(help="Account in the `ingest` group to push as.")
        ] = os.environ.get("MELRATER_PUSH_USER", ""),
        password_env: t.Annotated[
            str,
            typer.Option(
                help="Name of an environment variable holding the password "
                "(prompts when unset, so it need not be stored)."
            ),
        ] = "MELRATER_PUSH_PASSWORD",
        new: t.Annotated[
            bool, typer.Option(help="Only runs the server has never seen.")
        ] = False,
        force: t.Annotated[
            bool, typer.Option(help="Push even when the server is already current.")
        ] = False,
        dry_run: t.Annotated[
            bool, typer.Option(help="Report what would be pushed and send nothing.")
        ] = False,
    ) -> None:
        """Push ingested runs and their montages to a melrater server."""
        if not server:
            raise typer.BadParameter("--server (or MELRATER_PUSH_URL) is required")
        if not user:
            raise typer.BadParameter("--user (or MELRATER_PUSH_USER) is required")
        runs = list(
            Run.objects.filter(pk__in=run_ids).order_by("label")
            if run_ids
            else Run.objects.all().order_by("label")
        )
        if run_ids and len(runs) != len(set(run_ids)):
            missing = set(run_ids) - {run.pk for run in runs}
            raise typer.BadParameter(f"unknown run ids: {sorted(missing)}")
        if not runs:
            self.stdout.write("no runs to push")
            return

        password = os.environ.get(password_env) or typer.prompt(
            f"password for {user}", hide_input=True
        )
        target = push.PushTarget(base_url=server, username=user, password=password)

        failures: list[tuple[str, str]] = []
        n_pushed = 0
        n_skipped = 0
        with push.open_client(target) as client:
            try:
                index = push.fetch_index(client, target)
            except push.PushFailed as exc:
                raise CommandError(f"cannot reach {server}: {exc}") from exc

            for position, run in enumerate(runs, start=1):
                prefix = f"[{position}/{len(runs)}]"
                held = index.get(str(run.uuid))
                if not force and (
                    (new and held is not None) or held == str(run.montage_digest)
                ):
                    n_skipped += 1
                    self.stdout.write(
                        self.style.WARNING(f"{prefix} already current: {run.label}")
                    )
                    continue
                if dry_run:
                    n_pushed += 1
                    self.stdout.write(f"{prefix} would push {run.label}")
                    continue
                try:
                    payload, tar = selectors.run_bundle(run)
                    result = push.push_run(client, target, payload=payload, tar=tar)
                # a refused or undeliverable run must not end a batch of
                # hundreds; anything else is a bug and should still crash
                except (push.PushFailed, httpx.HTTPError, OSError) as exc:
                    failures.append((str(run.label), f"{type(exc).__name__}: {exc}"))
                    self.stderr.write(
                        self.style.ERROR(f"{prefix} FAILED {run.label}: {exc}")
                    )
                    continue
                n_pushed += 1
                verb = "created" if result["created"] else "refreshed"
                self.stdout.write(
                    self.style.SUCCESS(
                        f"{prefix} {verb} {run.label}: {result['n_montages']} montage(s)"
                    )
                )

        verb = "would push" if dry_run else "pushed"
        self.stdout.write("")
        self.stdout.write(
            f"{len(runs)} run(s): {n_pushed} {verb}, {n_skipped} already current, "
            f"{len(failures)} failed"
        )
        if failures:
            for label, reason in failures:
                self.stderr.write(self.style.ERROR(f"  {label}: {reason}"))
            raise CommandError(
                f"{len(failures)} of {len(runs)} run(s) could not be pushed"
            )
