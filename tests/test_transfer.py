"""The wire format's refusals: what is allowed to become a stored montage."""

import io
import tarfile

import pytest
from PIL import Image

from melrater.core import montage, transfer

FORMAT = "png"  # the codec every environment has; AVIF is covered separately


def _image_bytes(fmt: str = "PNG") -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (8, 8), (7, 7, 7)).save(buffer, format=fmt)
    return buffer.getvalue()


def _tar(members: list[tuple[str, bytes]]) -> io.BytesIO:
    buffer = io.BytesIO()
    transfer.write_montage_tar(members, buffer)
    buffer.seek(0)
    return buffer


def _read(archive: io.BytesIO, *, n_components: int = 1) -> list[tuple[str, bytes]]:
    return list(
        transfer.read_montage_tar(
            archive,
            n_components=n_components,
            montage_format=FORMAT,
            max_member_bytes=1024 * 1024,
        )
    )


# --- names --------------------------------------------------------------


def test_parse_montage_name_round_trips() -> None:
    # Assert: the pair has to stay each other's inverse
    assert montage.parse_montage_name(montage.montage_name(7, "axial", "avif")) == (
        7,
        "axial",
        "avif",
    )


def test_parse_montage_name_rejects_a_traversal_attempt() -> None:
    # Assert
    assert montage.parse_montage_name("../../etc/passwd.avif") is None


def test_parse_montage_name_rejects_a_trailing_newline() -> None:
    # Assert: `$` would match here and the rebuilt name would then differ from
    # the one that was checked, which is the whole point of using \Z
    assert montage.parse_montage_name("ic001_axial.avif\n") is None


def test_parse_montage_name_rejects_index_zero() -> None:
    # Assert: IC numbers are 1-based, so ic000 names no component
    assert montage.parse_montage_name("ic000_axial.avif") is None


def test_parse_montage_name_rejects_an_unknown_axis() -> None:
    # Assert
    assert montage.parse_montage_name("ic001_oblique.avif") is None


# --- image sniffing -----------------------------------------------------


def test_image_format_reads_png_magic() -> None:
    # Assert
    assert transfer.image_format(_image_bytes("PNG")) == "png"


def test_image_format_reads_avif_brands() -> None:
    # Assert
    pytest.importorskip("pillow_avif")
    assert transfer.image_format(_image_bytes("AVIF")) == "avif"


def test_image_format_rejects_a_non_image() -> None:
    # Assert: an HTML blob must not be able to reach /media/ under an image
    # extension, which is what would give it an HTML content type
    with pytest.raises(transfer.RejectedMontage):
        transfer.image_format(b"<html><script>alert(1)</script></html>")


# --- the tar ------------------------------------------------------------


def test_read_montage_tar_yields_the_rebuilt_name() -> None:
    # Act
    members = _read(_tar([("ic001_axial.png", _image_bytes())]))

    # Assert
    assert members == [("ic001_axial.png", _image_bytes())]


def test_read_montage_tar_rejects_a_name_that_is_not_a_montage() -> None:
    # Act / Assert
    with pytest.raises(transfer.RejectedMontage, match="not a montage name"):
        _read(_tar([("../escape.png", _image_bytes())]))


def test_read_montage_tar_rejects_a_component_outside_the_run() -> None:
    # Act / Assert
    with pytest.raises(transfer.RejectedMontage, match="component 9"):
        _read(_tar([("ic009_axial.png", _image_bytes())]))


def test_read_montage_tar_rejects_a_format_that_is_not_the_runs() -> None:
    # Arrange: a real PNG wearing an .avif name
    archive = _tar([("ic001_axial.avif", _image_bytes())])

    # Act / Assert
    with pytest.raises(transfer.RejectedMontage, match="the name says"):
        _read(archive)


def test_read_montage_tar_refuses_a_symlink_member() -> None:
    # Arrange: extractall() would follow this; the reader never calls it
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w") as tar:
        info = tarfile.TarInfo("ic001_axial.png")
        info.type = tarfile.SYMTYPE
        info.linkname = "/etc/passwd"
        tar.addfile(info)
    buffer.seek(0)

    # Act / Assert
    with pytest.raises(transfer.RejectedMontage, match="not a regular file"):
        _read(buffer)


def test_read_montage_tar_refuses_an_oversized_member() -> None:
    # Act / Assert: the size comes from the header, so nothing is read first
    with pytest.raises(transfer.RejectedMontage, match="over the"):
        list(
            transfer.read_montage_tar(
                _tar([("ic001_axial.png", _image_bytes())]),
                n_components=1,
                montage_format=FORMAT,
                max_member_bytes=8,
            )
        )


def test_read_montage_tar_stops_at_the_member_cap() -> None:
    # Arrange: four members for a one-component run, which allows three
    members = [
        (f"ic001_{axis}.png", _image_bytes())
        for axis in ("axial", "coronal", "sagittal")
    ]
    members.append(("ic001_axial.png", _image_bytes()))

    # Act / Assert
    with pytest.raises(transfer.RejectedMontage, match="more than 3 montages"):
        _read(_tar(members))


# --- the digest ---------------------------------------------------------


def test_digest_montages_ignores_order() -> None:
    # Assert: a directory listing's order must not change a run's identity
    assert montage.digest_montages([("a", b"1"), ("b", b"2")]) == (
        montage.digest_montages([("b", b"2"), ("a", b"1")])
    )


def test_digest_montages_changes_with_one_byte() -> None:
    # Assert: this is what makes a montage URL safe to cache forever
    assert montage.digest_montages([("a", b"1")]) != montage.digest_montages(
        [("a", b"2")]
    )


def test_digest_montages_changes_with_a_rename() -> None:
    # Assert
    assert montage.digest_montages([("a", b"1")]) != montage.digest_montages(
        [("b", b"1")]
    )
