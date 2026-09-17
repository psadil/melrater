"""The ingest API: who may push, what may be pushed, and what a push may touch."""

from pathlib import Path
from uuid import uuid4

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile

from melrater.core import montage, services
from melrater.core.models import Classification, Reviewer, Run
from melrater.core.schemas import RunPayload
from tests.conftest import N_COMPONENTS

pytestmark = pytest.mark.django_db

RUNS = "/api/v1/runs"


def _post(client, bundle, auth):
    payload, tar = bundle
    return client.post(
        RUNS,
        {
            "run": SimpleUploadedFile("run.json", payload),
            "montages": SimpleUploadedFile("montages.tar", tar),
        },
        **auth,
    )


def _as_new_run(bundle, **changes):
    """The same montages under a uuid the database has never seen."""
    payload, tar = bundle
    data = RunPayload.model_validate_json(payload)
    fresh = uuid4()
    data = data.model_copy(update={"uuid": fresh, "path": f"/data/{fresh}", **changes})
    return data.model_dump_json().encode(), tar


# --- who may push -------------------------------------------------------


def test_push_requires_credentials(client, push_bundle) -> None:
    # Act
    response = _post(client, push_bundle, {})

    # Assert
    assert response.status_code == 401


def test_push_rejects_an_account_outside_the_ingest_group(
    client, push_bundle, user
) -> None:
    # Arrange: a reviewer's password must not also be a way to write runs
    import base64

    token = base64.b64encode(b"rater:pw").decode()

    # Act
    response = _post(client, push_bundle, {"HTTP_AUTHORIZATION": f"Basic {token}"})

    # Assert
    assert response.status_code == 401


def test_push_rejects_a_revoked_account(
    client, push_bundle, ingest_auth, ingest_user
) -> None:
    # Arrange: revoking is taking the account back out of the group
    ingest_user.groups.clear()

    # Act
    response = _post(client, push_bundle, ingest_auth)

    # Assert
    assert response.status_code == 401


def test_push_is_closed_when_ingest_is_disabled(
    client, push_bundle, ingest_auth, settings
) -> None:
    # Arrange: the default outside a source checkout
    settings.INGEST_ENABLED = False

    # Act
    response = _post(client, push_bundle, ingest_auth)

    # Assert
    assert response.status_code == 401


def test_index_requires_credentials(client) -> None:
    # Act
    response = client.get(RUNS)

    # Assert
    assert response.status_code == 401


# --- the happy path -----------------------------------------------------


def test_index_reports_the_montage_digest(client, ingest_auth, ingested_run) -> None:
    # Act: this is what lets a client skip a run it has already sent
    response = client.get(RUNS, **ingest_auth)

    # Assert
    assert response.json() == [
        {
            "uuid": str(ingested_run.uuid),
            "label": ingested_run.label,
            "montage_digest": ingested_run.montage_digest,
        }
    ]


def test_index_carries_no_ratings(client, ingest_auth, ingested_run) -> None:
    # Assert: an ingest credential is a write credential, not a read one
    response = client.get(RUNS, **ingest_auth)
    assert "label" not in str(response.json()[0].get("classifications", ""))


def test_push_creates_the_run(client, ingest_auth, push_bundle) -> None:
    # Arrange
    bundle = _as_new_run(push_bundle)
    uuid = RunPayload.model_validate_json(bundle[0]).uuid

    # Act
    _post(client, bundle, ingest_auth)

    # Assert
    assert Run.objects.filter(uuid=uuid).exists()


def test_push_creates_every_component(client, ingest_auth, push_bundle) -> None:
    # Arrange
    bundle = _as_new_run(push_bundle)
    uuid = RunPayload.model_validate_json(bundle[0]).uuid

    # Act
    _post(client, bundle, ingest_auth)

    # Assert
    assert Run.objects.get(uuid=uuid).components.count() == N_COMPONENTS


def test_push_carries_the_fix_verdicts(client, ingest_auth, push_bundle) -> None:
    # Arrange
    bundle = _as_new_run(push_bundle)
    uuid = RunPayload.model_validate_json(bundle[0]).uuid

    # Act
    _post(client, bundle, ingest_auth)

    # Assert
    assert (
        Classification.objects.filter(
            component__run__uuid=uuid, reviewer__kind=Reviewer.Kind.FIX
        ).count()
        == N_COMPONENTS
    )


def test_push_stores_every_montage(
    client, ingest_auth, push_bundle, media_root: Path
) -> None:
    # Arrange
    bundle = _as_new_run(push_bundle)
    payload = RunPayload.model_validate_json(bundle[0])

    # Act
    _post(client, bundle, ingest_auth)

    # Assert: under the uuid and the content digest, where montage_url looks
    stored = media_root / "runs" / str(payload.uuid) / payload.montage_digest
    assert len(list(stored.iterdir())) == montage.montage_count(
        N_COMPONENTS, payload.backgrounds
    )


@pytest.fixture
def logged_in(client, user):
    """A reviewer's session, for checking what a push makes loadable."""
    client.force_login(user)
    return client


def test_pushed_montage_is_served_by_the_media_view(
    client, ingest_auth, push_bundle, logged_in
) -> None:
    # Arrange
    bundle = _as_new_run(push_bundle)
    _post(client, bundle, ingest_auth)
    payload = RunPayload.model_validate_json(bundle[0])

    # Act: close the loop — what was pushed is what reviewers load
    response = logged_in.get(
        f"/media/runs/{payload.uuid}/{payload.montage_digest}"
        f"/ic001_func_axial.{payload.montage_format}"
    )

    # Assert
    assert response.status_code == 200


def test_push_is_idempotent_on_uuid(client, ingest_auth, push_bundle) -> None:
    # Arrange: retrying an interrupted batch must not duplicate anything
    bundle = _as_new_run(push_bundle)

    # Act
    _post(client, bundle, ingest_auth)
    _post(client, bundle, ingest_auth)

    # Assert
    assert Run.objects.count() == 2  # the ingested fixture, plus this one


def test_push_reports_whether_it_created_the_run(
    client, ingest_auth, push_bundle
) -> None:
    # Arrange
    bundle = _as_new_run(push_bundle)
    _post(client, bundle, ingest_auth)

    # Act
    response = _post(client, bundle, ingest_auth)

    # Assert
    assert response.json()["created"] is False


# --- what a push may not do ---------------------------------------------


def test_push_leaves_a_human_rating_untouched(
    client, ingest_auth, ingested_run, push_bundle, user
) -> None:
    # Arrange: the server's side of the transfer — a rating made here has to
    # survive a bundle arriving from the laptop
    component = ingested_run.components.get(index=1)
    services.rate_component(user=user, component=component, label="Signal")
    before = Classification.objects.get(reviewer__user=user)

    # Act
    _post(client, push_bundle, ingest_auth)

    # Assert
    assert Classification.objects.filter(pk=before.pk, label=before.label).exists()


def test_push_does_not_rewrite_an_existing_run(
    client, ingest_auth, ingested_run, push_bundle
) -> None:
    # Arrange: a run that exists is only ever re-rendered, never rewritten
    payload = RunPayload.model_validate_json(push_bundle[0])
    payload.components[0].explained_var = 99.0

    # Act
    _post(client, (payload.model_dump_json().encode(), push_bundle[1]), ingest_auth)

    # Assert
    assert ingested_run.components.get(index=1).explained_var != 99.0


def test_push_refuses_to_change_the_component_count(
    client, ingest_auth, ingested_run, push_bundle
) -> None:
    # Arrange: dropping a component would cascade away its ratings
    payload = RunPayload.model_validate_json(push_bundle[0])
    payload.components.pop()

    # Act
    response = _post(
        client, (payload.model_dump_json().encode(), push_bundle[1]), ingest_auth
    )

    # Assert
    assert response.status_code == 409


def test_push_refuses_a_fix_name_a_human_already_holds(
    client, ingest_auth, push_bundle
) -> None:
    # Arrange: a name collision would make every later rating by that human
    # raise IntegrityError, which is worse than refusing the push
    payload = RunPayload.model_validate_json(push_bundle[0])
    fix = payload.fix[0]
    Reviewer.objects.filter(kind=Reviewer.Kind.FIX).delete()
    Reviewer.objects.create(
        kind=Reviewer.Kind.HUMAN,
        name=f"{fix.model} @ thr{fix.threshold}",
    )

    # Act
    response = _post(client, _as_new_run(push_bundle), ingest_auth)

    # Assert
    assert response.status_code == 409


def test_push_refuses_an_unknown_field(client, ingest_auth, push_bundle) -> None:
    # Arrange: a stale client still setting a montage revision must fail loudly
    payload = RunPayload.model_validate_json(push_bundle[0]).model_dump()
    payload["montage_rev"] = 3

    # Act
    import json

    response = _post(
        client, (json.dumps(payload, default=str).encode(), push_bundle[1]), ingest_auth
    )

    # Assert
    assert response.status_code == 422


def test_push_refuses_montages_that_do_not_match_the_digest(
    client, ingest_auth, push_bundle
) -> None:
    # Arrange: what a truncated upload looks like
    payload = RunPayload.model_validate_json(push_bundle[0])
    payload = payload.model_copy(update={"montage_digest": "0" * 16})

    # Act
    response = _post(
        client,
        _as_new_run((payload.model_dump_json().encode(), push_bundle[1])),
        ingest_auth,
    )

    # Assert
    assert response.status_code == 409


def test_push_writes_no_rows_when_the_montages_are_refused(
    client, ingest_auth, push_bundle
) -> None:
    # Arrange
    payload = RunPayload.model_validate_json(push_bundle[0])
    payload = payload.model_copy(update={"montage_digest": "0" * 16})
    bundle = _as_new_run((payload.model_dump_json().encode(), push_bundle[1]))

    # Act
    _post(client, bundle, ingest_auth)

    # Assert
    assert Run.objects.count() == 1  # only the ingested fixture


def test_push_leaves_no_montages_when_the_montages_are_refused(
    client, ingest_auth, push_bundle, media_root: Path
) -> None:
    # Arrange
    payload = RunPayload.model_validate_json(push_bundle[0])
    payload = payload.model_copy(update={"montage_digest": "0" * 16})
    bundle = _as_new_run((payload.model_dump_json().encode(), push_bundle[1]))
    uuid = RunPayload.model_validate_json(bundle[0]).uuid

    # Act
    _post(client, bundle, ingest_auth)

    # Assert
    assert not (media_root / "runs" / str(uuid)).exists()


def test_push_refuses_a_tar_over_the_size_cap(
    client, ingest_auth, push_bundle, settings
) -> None:
    # Arrange
    settings.INGEST_MAX_TAR_BYTES = 16

    # Act
    response = _post(client, _as_new_run(push_bundle), ingest_auth)

    # Assert
    assert response.status_code == 413


def test_push_refuses_more_components_than_the_cap(
    client, ingest_auth, push_bundle, settings
) -> None:
    # Arrange
    settings.INGEST_MAX_COMPONENTS = 1

    # Act
    response = _post(client, _as_new_run(push_bundle), ingest_auth)

    # Assert
    assert response.status_code == 409
