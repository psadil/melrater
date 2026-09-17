"""The wire format for one run's montages: a tar of ``ic<NNN>_<bg>_<axis>.<ext>``.

Deliberately Django-free, like ``montage.py``: this is the module that decides
whether bytes arriving from outside are allowed to become files, and it is
easier to trust — and to test, with no database — when it depends on nothing.

The guarantee it provides is that **a name chosen by the sender never reaches a
storage path**. Every yielded name is rebuilt by ``montage.montage_name`` from
an integer, a ``BACKGROUNDS`` key, an ``AXES`` key and a format sniffed from
the bytes themselves; a member whose own name does not parse is refused rather
than sanitised.
"""

from __future__ import annotations

import io
import tarfile
from collections.abc import Iterable, Iterator, Sequence
from typing import IO

from melrater.core.montage import montage_count, montage_name, parse_montage_name

#: Sniffed rather than trusted. The stored extension comes from these, so a
#: blob cannot pick its own Content-Type by picking its own file name.
_PNG_MAGIC = b"\x89PNG\r\n\x1a\n"
_AVIF_BRANDS = frozenset({b"avif", b"avis"})


class RejectedMontage(Exception):
    """A tar member is not an acceptable montage. Carries the reason."""


def image_format(data: bytes) -> str:
    """``'avif'`` or ``'png'`` from the leading bytes. Raises otherwise.

    A magic-byte sniff rather than a Pillow decode: the sender is
    authenticated, the whole set is verified by digest afterwards, and a real
    decode would be ~288 of them per run on a two-core box.
    """
    if data.startswith(_PNG_MAGIC):
        return "png"
    if _is_avif(data):
        return "avif"
    raise RejectedMontage("not a PNG or AVIF image")


def _is_avif(data: bytes) -> bool:
    """ISO-BMFF ``ftyp`` box whose major or compatible brand is AVIF."""
    if len(data) < 12 or data[4:8] != b"ftyp":
        return False
    if data[8:12] in _AVIF_BRANDS:
        return True
    # compatible brands fill the rest of the ftyp box, four bytes each
    box_size = min(int.from_bytes(data[0:4], "big"), len(data))
    brands = (data[at : at + 4] for at in range(16, box_size - 3, 4))
    return any(brand in _AVIF_BRANDS for brand in brands)


def read_montage_tar(
    fileobj: IO[bytes],
    *,
    n_components: int,
    backgrounds: Sequence[str],
    montage_format: str,
    max_member_bytes: int,
) -> Iterator[tuple[str, bytes]]:
    """Yield ``(rebuilt name, bytes)`` for each montage in an uploaded tar.

    Streamed (``mode="r|"``), so peak memory is one member rather than the
    whole ~20 MB archive. Refuses, in this order and before reading any
    payload: anything that is not a regular file (which is what excludes
    symlinks, hard links and device nodes), an oversized member, more members
    than the run can have, a name that is not a montage name, a component index
    outside the run, a background the run did not declare, and a format that
    disagrees with the run's.
    """
    max_members = montage_count(n_components, backgrounds)
    seen = 0
    with tarfile.open(fileobj=fileobj, mode="r|") as tar:
        for member in tar:
            if not member.isfile():
                raise RejectedMontage(f"{member.name!r} is not a regular file")
            if member.size > max_member_bytes:
                raise RejectedMontage(
                    f"{member.name!r} is {member.size} bytes, over the "
                    f"{max_member_bytes}-byte limit"
                )
            seen += 1
            if seen > max_members:
                raise RejectedMontage(
                    f"more than {max_members} montages for {n_components} components"
                )
            parsed = parse_montage_name(member.name)
            if parsed is None:
                raise RejectedMontage(f"{member.name!r} is not a montage name")
            index, background, axis, ext = parsed
            if index > n_components:
                raise RejectedMontage(
                    f"component {index} in a run with {n_components} components"
                )
            if background not in backgrounds:
                raise RejectedMontage(
                    f"a {background} montage in a run that declares {list(backgrounds)}"
                )
            handle = tar.extractfile(member)
            if handle is None:  # unreachable for isfile(), but typed Optional
                raise RejectedMontage(f"{member.name!r} could not be read")
            data = handle.read()
            sniffed = image_format(data)
            if sniffed != ext or sniffed != montage_format:
                raise RejectedMontage(
                    f"{member.name!r} is a {sniffed}, but the run is "
                    f"{montage_format} and the name says {ext}"
                )
            yield montage_name(index, background, axis, sniffed), data


def write_montage_tar(members: Iterable[tuple[str, bytes]], fileobj: IO[bytes]) -> int:
    """Pack ``(name, bytes)`` pairs into ``fileobj``. Returns the count.

    Basenames only: the run's uuid and digest are in the URL, not the archive,
    so the receiver decides where the files land.
    """
    count = 0
    with tarfile.open(fileobj=fileobj, mode="w") as tar:
        for name, data in members:
            info = tarfile.TarInfo(name)
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
            count += 1
    return count
