"""Where one run's montages live under ``MEDIA_ROOT``.

Montages are ordinary Django media: written through ``default_storage`` and
served by ``views.media_file`` out of ``settings.MEDIA_ROOT``. This module owns
nothing but their *layout*, so that the naming rule lives in one place instead
of being spelled out at every call site.

The layout is content-addressed::

    runs/<Run.uuid>/<Run.montage_digest>/ic007_axial.avif

``uuid`` rather than a primary key because a run loaded into another database
gets a fresh id and its images have to survive that. ``digest`` — a fingerprint
of the rendered bytes, see ``montage.digest_montages`` — because it makes a
re-render additive: the new set is written alongside the old one and the old
one is dropped only once the row points at the new directory. Nothing is ever
overwritten in place, so a montage URL never changes what it means and can be
served ``immutable``.

Deliberately separate from ``montage.py``: that module stays Django-free so
spawn-based render workers can re-import it cheaply, and it writes into a plain
temporary directory that this module then ingests.
"""

from __future__ import annotations

from contextlib import suppress
from pathlib import Path
from uuid import UUID

from django.core.files.base import ContentFile
from django.core.files.storage import Storage, default_storage


def montage_storage() -> Storage:
    return default_storage


def run_root(run_uuid: UUID | str) -> str:
    """Storage-relative directory holding every digest of one run."""
    return f"runs/{run_uuid}/"


def run_prefix(run_uuid: UUID | str, digest: str) -> str:
    """Storage-relative directory holding one rendered set of montages."""
    return f"{run_root(run_uuid)}{digest}/"


def save(name: str, data: bytes) -> None:
    """Write ``data`` at ``name``, replacing anything already there.

    ``Storage.save`` on its own would *rename* around a collision
    (``ic001_axial_a8Fk2p.avif``), which would leave a re-pushed run serving
    its old images forever.
    """
    storage = montage_storage()
    if storage.exists(name):
        storage.delete(name)
    storage.save(name, ContentFile(data))


def url(name: str) -> str:
    return montage_storage().url(name)


def stored_run_uuids() -> list[str]:
    """Every run directory in the store, named or not ([] before any exist)."""
    return _listdir("runs/")[0]


def digests_for_run(run_uuid: UUID | str) -> list[str]:
    """Every stored digest directory for one run ([] when there are none)."""
    return _listdir(run_root(run_uuid))[0]


def names_for_run(run_uuid: UUID | str, digest: str) -> list[str]:
    """Every stored montage name in one digest ([] when there are none)."""
    prefix = run_prefix(run_uuid, digest)
    return [prefix + name for name in _listdir(prefix)[1]]


def store_directory(run_uuid: UUID | str, digest: str, staged: Path) -> None:
    """Ingest a freshly rendered directory as one run's ``digest`` set."""
    prefix = run_prefix(run_uuid, digest)
    for rendered in sorted(staged.iterdir()):
        if rendered.is_file():
            save(prefix + rendered.name, rendered.read_bytes())


def delete_digest(run_uuid: UUID | str, digest: str) -> None:
    """Remove one rendered set. Safe when it is not there."""
    storage = montage_storage()
    for name in names_for_run(run_uuid, digest):
        storage.delete(name)
    _remove_empty_directory(run_prefix(run_uuid, digest))
    # and the run's own directory, if that was its last set — rmdir simply
    # fails, harmlessly, while other digests are still there
    _remove_empty_directory(run_root(run_uuid))


def retain_digest(run_uuid: UUID | str, keep: str) -> None:
    """Drop every rendered set of one run except ``keep``.

    Called after the row has been pointed at ``keep``, never before: until
    then the old set is what reviewers are still being served.
    """
    for digest in digests_for_run(run_uuid):
        if digest != keep:
            delete_digest(run_uuid, digest)


def delete_run(run_uuid: UUID | str) -> None:
    """Remove every montage belonging to one run. Safe when there are none."""
    for digest in digests_for_run(run_uuid):
        delete_digest(run_uuid, digest)
    _remove_empty_directory(run_root(run_uuid))


def _remove_empty_directory(prefix: str) -> None:
    """Drop the directory the deleted files were in, on a filesystem backend.

    ``Storage`` has no notion of one, because an object store has no
    directories — so this is best-effort and does nothing at all elsewhere.
    Left behind, an empty directory would keep showing up in
    ``digests_for_run`` and read as a montage set that is merely missing its
    files.
    """
    storage = montage_storage()
    try:
        path = Path(storage.path(prefix))
    except NotImplementedError:
        return
    with suppress(OSError):
        path.rmdir()


def _listdir(prefix: str) -> tuple[list[str], list[str]]:
    try:
        return montage_storage().listdir(prefix)
    except (FileNotFoundError, NotADirectoryError):
        return [], []
