"""The push client: what goes on the wire, and what is worth retrying."""

import base64
import io
import tarfile

import httpx
import pytest
from django.core.management import call_command
from django.core.management.base import CommandError

from melrater.core import montage, push
from melrater.core.models import Run
from tests.conftest import N_COMPONENTS

TARGET = push.PushTarget(
    base_url="https://box.invalid", username="pusher", password="pw"
)


def _client(handler) -> httpx.Client:
    return httpx.Client(
        auth=(TARGET.username, TARGET.password),
        transport=httpx.MockTransport(handler),
    )


def _ok(_request: httpx.Request) -> httpx.Response:
    return httpx.Response(
        200,
        json={
            "uuid": "00000000-0000-0000-0000-000000000000",
            "label": "x",
            "created": True,
            "montage_digest": "abc",
            "n_montages": 3,
        },
    )


# --- the request itself -------------------------------------------------


def test_push_run_authenticates_as_the_ingest_account() -> None:
    # Arrange
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.headers["authorization"])
        return _ok(request)

    # Act
    push.push_run(_client(handler), TARGET, payload=b"{}", tar=b"tar")

    # Assert
    assert seen == [f"Basic {base64.b64encode(b'pusher:pw').decode()}"]


def test_push_run_sends_both_parts_as_files() -> None:
    # Arrange
    seen: list[bytes] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.content)
        return _ok(request)

    # Act
    push.push_run(_client(handler), TARGET, payload=b'{"a":1}', tar=b"tarbytes")

    # Assert: file parts, so DATA_UPLOAD_MAX_MEMORY_SIZE never sees the JSON
    assert b'filename="run.json"' in seen[0] and b'filename="montages.tar"' in seen[0]


def test_fetch_index_maps_uuid_to_digest() -> None:
    # Arrange
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, json=[{"uuid": "u", "label": "l", "montage_digest": "d"}]
        )

    # Act / Assert
    assert push.fetch_index(_client(handler), TARGET) == {"u": "d"}


# --- what is worth retrying ---------------------------------------------


def test_push_run_does_not_retry_a_refusal() -> None:
    # Arrange: a 409 is the server saying the run is wrong, so sending it
    # again would only be wrong twice
    attempts: list[int] = []

    def handler(_request: httpx.Request) -> httpx.Response:
        attempts.append(1)
        return httpx.Response(409, json={"detail": "component count changed"})

    # Act
    with pytest.raises(push.PushFailed):
        push.push_run(_client(handler), TARGET, payload=b"{}", tar=b"t")

    # Assert
    assert len(attempts) == 1


def test_push_run_retries_a_server_error(monkeypatch) -> None:
    # Arrange
    monkeypatch.setattr(push, "BACKOFF", 0.0)
    attempts: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        attempts.append(1)
        return _ok(request) if len(attempts) > 1 else httpx.Response(503)

    # Act
    push.push_run(_client(handler), TARGET, payload=b"{}", tar=b"t")

    # Assert
    assert len(attempts) == 2


def test_push_run_gives_up_after_the_retry_budget(monkeypatch) -> None:
    # Arrange
    monkeypatch.setattr(push, "BACKOFF", 0.0)

    def handler(_request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectTimeout("no route")

    # Act / Assert
    with pytest.raises(push.PushFailed, match="gave up"):
        push.push_run(_client(handler), TARGET, payload=b"{}", tar=b"t")


# --- the command --------------------------------------------------------

pytestmark = pytest.mark.django_db


@pytest.fixture
def served(monkeypatch):
    """Stand a fake deployment behind `push_runs`, and record what it hears."""
    requests: list[httpx.Request] = []
    state: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.method == "GET":
            return httpx.Response(
                200,
                json=[
                    {"uuid": uuid, "label": "x", "montage_digest": digest}
                    for uuid, digest in state.items()
                ],
            )
        return _ok(request)

    monkeypatch.setattr(push, "open_client", lambda target: _client(handler))
    monkeypatch.setenv("MELRATER_PUSH_PASSWORD", "pw")
    return requests, state


def test_push_runs_sends_a_tar_of_every_montage(
    served, ingested_run: Run, media_root
) -> None:
    # Arrange
    requests, _ = served

    # Act
    call_command("push_runs", "--server", "https://box.invalid", "--user", "pusher")

    # Assert
    body = requests[-1].content
    marker = b'filename="montages.tar"\r\nContent-Type: application/x-tar\r\n\r\n'
    tar = body.split(marker, 1)[1].rsplit(b"\r\n--", 1)[0]
    with tarfile.open(fileobj=io.BytesIO(tar)) as archive:
        assert len(archive.getnames()) == montage.montage_count(
            N_COMPONENTS, ("func",), montage.SMOOTHINGS
        )


def test_push_runs_skips_a_run_the_server_is_current_on(
    served, ingested_run: Run, media_root
) -> None:
    # Arrange
    requests, state = served
    state[str(ingested_run.uuid)] = str(ingested_run.montage_digest)

    # Act
    call_command("push_runs", "--server", "https://box.invalid", "--user", "pusher")

    # Assert: only the index was fetched — nothing was uploaded
    assert [r.method for r in requests] == ["GET"]


def test_push_runs_sends_a_run_whose_montages_changed(
    served, ingested_run: Run, media_root
) -> None:
    # Arrange: the server holds the run, but an older render of it
    requests, state = served
    state[str(ingested_run.uuid)] = "0" * 16

    # Act
    call_command("push_runs", "--server", "https://box.invalid", "--user", "pusher")

    # Assert
    assert [r.method for r in requests] == ["GET", "POST"]


def test_push_runs_dry_run_uploads_nothing(
    served, ingested_run: Run, media_root
) -> None:
    # Arrange
    requests, _ = served

    # Act
    call_command(
        "push_runs", "--server", "https://box.invalid", "--user", "pusher", "--dry-run"
    )

    # Assert
    assert [r.method for r in requests] == ["GET"]


def test_push_runs_reports_a_failure_by_exiting_non_zero(
    monkeypatch, ingested_run: Run, media_root
) -> None:
    # Arrange
    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "GET":
            return httpx.Response(200, json=[])
        return httpx.Response(409, json={"detail": "nope"})

    monkeypatch.setattr(push, "open_client", lambda target: _client(handler))
    monkeypatch.setenv("MELRATER_PUSH_PASSWORD", "pw")

    # Act / Assert
    with pytest.raises(CommandError, match="could not be pushed"):
        call_command("push_runs", "--server", "https://box.invalid", "--user", "pusher")


# --- one real round trip ------------------------------------------------


@pytest.mark.django_db(transaction=True)
def test_push_client_creates_the_run_over_http(
    live_server, ingest_user, media_root, ingested_run: Run
) -> None:
    # Arrange: live_server shares this test's database, so the push is given a
    # uuid that database has never seen and creates a *second* run
    from uuid import uuid4

    from melrater.core import selectors
    from melrater.core.schemas import RunPayload

    payload, tar = selectors.run_bundle(ingested_run)
    fresh = uuid4()
    data = RunPayload.model_validate_json(payload).model_copy(
        update={"uuid": fresh, "path": f"/data/{fresh}"}
    )
    target = push.PushTarget(base_url=live_server.url, username="pusher", password="pw")

    # Act: real httpx, real multipart, real Django, real storage
    with push.open_client(target) as client:
        push.push_run(client, target, payload=data.model_dump_json().encode(), tar=tar)

    # Assert
    assert Run.objects.filter(uuid=fresh).exists()
