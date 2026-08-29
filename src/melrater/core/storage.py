"""The montage object store.

Montages are the only files melrater writes at runtime, and they dominate its
footprint (~20 MB per run, ~6 GB for a few hundred). Every read and write of
them goes through this module rather than through ``MEDIA_ROOT`` directly, so
that moving them to object storage later is an edit to ``settings.STORAGES``
and nothing else — no model change, no migration.

Files are keyed by ``Run.uuid``, not by ``Run.pk``: a run loaded into another
database gets a fresh primary key, and montage paths have to survive that (see
``export_runs`` and deploy.md).

Deliberately thin, and deliberately separate from ``montage.py``: that module
stays Django-free so spawn-based render workers can re-import it cheaply, and
it writes into a plain temporary directory that this module then ingests.
"""

from __future__ import annotations

from pathlib import Path
from uuid import UUID

from django.core.files.base import ContentFile
from django.core.files.storage import Storage, storages

STORAGE_ALIAS = "montages"


def montage_storage() -> Storage:
    return storages[STORAGE_ALIAS]


def run_prefix(run_uuid: UUID | str) -> str:
    """Storage-relative directory holding one run's montages."""
    return f"runs/{run_uuid}/"


def save(name: str, data: bytes) -> None:
    """Write ``data`` at ``name``, replacing anything already there.

    ``Storage.save`` on its own would *rename* around a collision
    (``ic001_axial_a8Fk2p.avif``), which would leave a re-rendered run serving
    its old images forever.
    """
    storage = montage_storage()
    if storage.exists(name):
        storage.delete(name)
    storage.save(name, ContentFile(data))


def url(name: str) -> str:
    return montage_storage().url(name)


def names_for_run(run_uuid: UUID | str) -> list[str]:
    """Every stored montage name for one run ([] when the run has none)."""
    storage = montage_storage()
    prefix = run_prefix(run_uuid)
    try:
        _, files = storage.listdir(prefix)
    except (FileNotFoundError, NotADirectoryError):
        return []
    return [prefix + name for name in files]


def store_directory(run_uuid: UUID | str, staged: Path) -> None:
    """Ingest a freshly rendered directory into the store under ``run_uuid``."""
    prefix = run_prefix(run_uuid)
    for rendered in sorted(staged.iterdir()):
        if rendered.is_file():
            save(prefix + rendered.name, rendered.read_bytes())


def delete_run(run_uuid: UUID | str) -> None:
    """Remove every montage belonging to one run. Safe when there are none."""
    storage = montage_storage()
    for name in names_for_run(run_uuid):
        storage.delete(name)


def delete_extension(run_uuid: UUID | str, extension: str) -> None:
    """Remove a run's montages in one format, after a re-render changed it."""
    storage = montage_storage()
    suffix = f".{extension}"
    for name in names_for_run(run_uuid):
        if name.endswith(suffix):
            storage.delete(name)
