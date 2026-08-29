"""The run-ingest API: how runs get from the laptop onto the server.

Two operations, and nothing that reads a rating. A run is pushed as one
multipart request carrying two *file* parts — its rows as JSON and its
montages as a tar — which is what makes the whole thing atomic per run: a
push either lands or is retried, and retries are idempotent on ``Run.uuid``.

Both parts are files on purpose. ``DATA_UPLOAD_MAX_MEMORY_SIZE`` is calculated
excluding file upload data, so a 96-component run's ~2.5 MB of JSON travels
without weakening that 2.5 MB guard for ``/accounts/login/`` and the rating
POST, which is what raising the setting would have done.

Thin, like ``views.py``: parse, hand to a service, return a schema.
"""

from __future__ import annotations

import logging

from django.conf import settings
from django.contrib.auth import authenticate
from django.contrib.auth.models import User
from django.http import HttpRequest, HttpResponse
from ninja import File, NinjaAPI, UploadedFile
from ninja.security import HttpBasicAuth
from pydantic import ValidationError

from melrater.core import selectors, services, transfer
from melrater.core.schemas import PushResult, RunPayload, RunSummary

logger = logging.getLogger("melrater.ingest")

#: Membership in this stock auth Group is what grants run ingest. A group
#: rather than `is_staff`, which would additionally open /admin/, and rather
#: than a bespoke token model, which would have bypassed django-axes entirely.
INGEST_GROUP = "ingest"


class IngestAuth(HttpBasicAuth):
    """HTTP Basic against an ordinary Django account in the ingest group.

    Going through ``django.contrib.auth.authenticate`` is the point: it puts
    ``AxesStandaloneBackend`` in front of the password check, so a wrong
    password here counts towards the same (address, username) lockout as a
    wrong password on the login form. A bearer token would have had no
    throttling at all.

    The account is deliberately neither staff nor superuser, so what a leaked
    push password buys is run ingest and the reviewing UI — not /admin/, and
    not the ability to rewrite or delete anyone's ratings.
    """

    def authenticate(
        self, request: HttpRequest, username: str, password: str
    ) -> User | None:
        if not settings.INGEST_ENABLED:
            return None
        user = authenticate(request, username=username, password=password)
        if not isinstance(user, User):
            return None
        if not user.groups.filter(name=INGEST_GROUP).exists():
            logger.warning("ingest: %s is not in the %s group", username, INGEST_GROUP)
            return None
        return user


api = NinjaAPI(
    title="melrater ingest",
    version="1",
    auth=IngestAuth(),
    urls_namespace="ingest",
    # No browsable docs: the Swagger UI loads its assets from a CDN that
    # SECURE_CSP blocks anyway. The schema itself is CSP-immune and is enough
    # to read the contract from, so it is available in development only.
    docs_url=None,
    openapi_url="/openapi.json" if settings.DEBUG else None,
)


@api.exception_handler(services.PushRejected)
def _on_push_rejected(request: HttpRequest, exc: Exception) -> HttpResponse:
    logger.warning("ingest: rejected a push: %s", exc)
    return api.create_response(request, {"detail": str(exc)}, status=409)


@api.exception_handler(transfer.RejectedMontage)
def _on_rejected_montage(request: HttpRequest, exc: Exception) -> HttpResponse:
    logger.warning("ingest: rejected a montage: %s", exc)
    return api.create_response(request, {"detail": str(exc)}, status=415)


@api.exception_handler(services.PushTooLarge)
def _on_push_too_large(request: HttpRequest, exc: Exception) -> HttpResponse:
    logger.warning("ingest: refused an oversized push: %s", exc)
    return api.create_response(request, {"detail": str(exc)}, status=413)


@api.exception_handler(ValidationError)
def _on_invalid_payload(request: HttpRequest, exc: Exception) -> HttpResponse:
    logger.warning("ingest: refused an invalid payload")
    errors = exc.errors(include_url=False) if isinstance(exc, ValidationError) else []
    return api.create_response(request, {"detail": errors}, status=422)


@api.get("/runs", response=list[RunSummary])
def run_index(request: HttpRequest) -> list[RunSummary]:
    """Every run this server holds, so a client can skip what it has sent.

    ``montage_digest`` is derived from the montage bytes, so it means the same
    thing in both databases — "already present and unchanged" is an exact
    comparison rather than a guess from timestamps or a revision counter that
    each side increments on its own.
    """
    return selectors.run_summaries()


@api.post("/runs", response=PushResult)
def push_run(
    request: HttpRequest,
    run: File[UploadedFile],
    montages: File[UploadedFile],
) -> PushResult:
    """Take one run: its rows as JSON, its montages as a tar.

    Order is montages first, rows last — the same ordering, for the same
    reason, as `services.ingest_run`. Until the row exists nothing names those
    files, so a push that dies partway leaves unreachable orphans rather than
    a run whose page is a screen of broken images.
    """
    if montages.size and montages.size > settings.INGEST_MAX_TAR_BYTES:
        raise services.PushTooLarge(
            f"the montages are over {settings.INGEST_MAX_TAR_BYTES} bytes"
        )
    # a ValidationError here is answered by the handler above, so the operation
    # keeps one honest return type instead of sometimes being a raw response
    payload = RunPayload.model_validate_json(run.read())

    existing = selectors.run_by_uuid(payload.uuid)
    services.check_pushed_run(run=existing, payload=payload)

    montages.seek(0)
    if montages.file is None:  # unreachable for an uploaded part; narrows for ty
        raise transfer.RejectedMontage("no montages were uploaded")
    stored = services.store_pushed_montages(
        run_uuid=payload.uuid, payload=payload, tar=montages.file
    )
    if existing is None:
        row = services.create_pushed_run(payload=payload)
    else:
        row = services.refresh_pushed_run(run=existing, payload=payload)
    return PushResult(
        uuid=row.uuid,
        label=str(row.label),
        created=existing is None,
        montage_digest=str(row.montage_digest),
        n_montages=stored,
    )
