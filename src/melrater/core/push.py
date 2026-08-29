"""The client half of the ingest API: what `push_runs` talks over.

No Django imports, deliberately. Everything here takes bytes that a selector
already produced, which keeps the HTTP concerns testable against
``httpx.MockTransport`` with no database in the picture.

One run is one request carrying two file parts, so the failure model is simply
"it landed or it did not". Retries are safe because the server is idempotent on
``Run.uuid`` — but only network-level failures are retried: a 4xx is the server
saying the run is wrong, and sending it again would only be wrong twice.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any

import httpx

#: Generous, because a request carries ~20 MB of montages over a domestic
#: uplink and the server hashes every one of them on arrival.
DEFAULT_TIMEOUT = 300.0
RETRIES = 3
BACKOFF = 2.0


class PushFailed(Exception):
    """The server refused a run, or stopped answering about it."""


@dataclass(frozen=True)
class PushTarget:
    base_url: str
    username: str
    password: str
    timeout: float = DEFAULT_TIMEOUT

    @property
    def runs_url(self) -> str:
        return f"{self.base_url.rstrip('/')}/api/v1/runs"


def open_client(target: PushTarget) -> httpx.Client:
    """A client that verifies TLS. There is deliberately no way to turn that off.

    The deployment holds a short-lived certificate for a bare IP address, so
    the one moment anybody would reach for `--insecure` is exactly the moment
    the certificate has expired and the connection is genuinely unprotected —
    which is when the push password would be handed to whoever answered.
    """
    return httpx.Client(
        auth=(target.username, target.password),
        timeout=target.timeout,
        follow_redirects=False,
    )


def fetch_index(client: httpx.Client, target: PushTarget) -> dict[str, str]:
    """``{uuid: montage_digest}`` for every run the server already holds."""
    response = client.get(target.runs_url)
    if response.status_code != 200:
        raise PushFailed(f"index: HTTP {response.status_code} {response.text[:200]}")
    return {row["uuid"]: row["montage_digest"] for row in response.json()}


def push_run(
    client: httpx.Client, target: PushTarget, *, payload: bytes, tar: bytes
) -> dict[str, Any]:
    """Send one run. Retries transport failures and 5xx; never a 4xx."""
    files = {
        "run": ("run.json", payload, "application/json"),
        "montages": ("montages.tar", tar, "application/x-tar"),
    }
    last = ""
    for attempt in range(RETRIES):
        try:
            response = client.post(target.runs_url, files=files)
        except httpx.HTTPError as exc:
            last = f"{type(exc).__name__}: {exc}"
        else:
            if response.status_code == 200:
                return response.json()
            if response.status_code < 500:
                raise PushFailed(f"HTTP {response.status_code}: {_detail(response)}")
            last = f"HTTP {response.status_code}: {_detail(response)}"
        if attempt < RETRIES - 1:
            time.sleep(BACKOFF * (attempt + 1))
    raise PushFailed(f"gave up after {RETRIES} attempts — {last}")


def _detail(response: httpx.Response) -> str:
    try:
        return str(response.json().get("detail", response.text[:200]))
    except ValueError:
        return response.text[:200]
