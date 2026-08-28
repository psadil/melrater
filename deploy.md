# Deploying melrater

One container, SQLite, two bind mounts. The image both serves the app and runs
management commands; everything durable lives on the host.

## What is durable, and where

`settings.py` computes both paths from `BASE_DIR`, which is `/app` in the image.
Neither is configurable by environment variable, so they are mount points:

| In the container | On the host           | Holds                                        |
| ---------------- | --------------------- | -------------------------------------------- |
| `/app/db`        | `/srv/melrater/db`    | `db.sqlite3` + its `-wal`/`-shm` sidecars     |
| `/app/media`     | `/srv/melrater/media` | pre-rendered montages, `runs/<run_pk>/*.avif` |

Everything else — the pixi environment, `src/`, the collected static files — is
baked into the image and replaced wholesale on redeploy.

Montage paths are derived from the run's primary key, so nothing absolute is
stored in the database and the pair transfers to any machine. `Run.path` does
hold the laptop path of the source derivatives, but no view dereferences it —
only re-ingest and `rerender_montages` do, and neither runs on the server.

## 1. Build the image [laptop]

```bash
docker buildx build --platform=linux/amd64 \
  --provenance=mode=max --sbom=true \
  -t psadil/melrater:latest .
```

`--platform` is not optional: `pixi.lock` resolves `osx-arm64` and `linux-64`
only. The build compiles bidslake from Rust under emulation, so the first one
takes a few minutes; afterwards only a source edit invalidates the last layers.

`mode=max` is the fuller of the two provenance modes — measured against this
build, it adds the Dockerfile source, all ten build steps and a third
digest-pinned base image over `mode=min`; both record the git revision and
remote. `--sbom=true` catalogues 132 packages. Two caveats worth knowing:
`mode=max` also records `--build-arg` **values**, so never pass a secret that
way; and attestations make the output a manifest list, which `--load` can only
store when Docker's containerd image store is on (Docker Desktop → Settings →
General). If it is off, `--load` cannot take the index — push instead.

Smoke-test it before shipping — emulation produces a wrong binary far more
readily than a failed build:

```bash
docker run --rm --entrypoint python psadil/melrater:latest -c "import bidslake, polars, numpy, nibabel, PIL, pillow_avif; print('ok')"
```

## 2. Prepare the box [server]

Docker, from the official repository:

```bash
apt-get update && apt-get install -y ca-certificates curl rsync sqlite3
install -m 0755 -d /etc/apt/keyrings
curl -fsSL https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc && chmod a+r /etc/apt/keyrings/docker.asc
echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/ubuntu $(. /etc/os-release && echo "$VERSION_CODENAME") stable" > /etc/apt/sources.list.d/docker.list
apt-get update && apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
```

Then the host directories. The container runs as uid/gid 57439 (`mambauser`),
and that account does not exist here, so chown by number — WAL creates its
sidecars *in the directory*, so the directory itself must be writable, not just
the database file:

```bash
mkdir -p /srv/melrater/{db,media,backups}
chown -R 57439:57439 /srv/melrater/db /srv/melrater/media /srv/melrater/backups
chmod 750 /srv/melrater /srv/melrater/db /srv/melrater/media /srv/melrater/backups
```

`backups` needs the same 750 as the rest: the nightly job below writes a
complete copy of the database into it.

## 3. Get the image onto the box

Use the registry if you want the provenance and SBOM to survive the trip:
`docker save` silently drops attestations, so the SSH pipe below delivers a
runnable image but an unattested one.

With a registry (`docker login` on the laptop first; Docker Hub repos are
public by default, so the server needs no credentials to pull):

```bash
docker push psadil/melrater:latest      # [laptop]
docker pull psadil/melrater:latest      # [server]
```

Without one, pipe it straight over SSH from the laptop — this needs docker
already installed on the box, which step 2 did:

```bash
docker save psadil/melrater:latest | ssh root@SERVER 'docker load'
```

That moves about 535 MiB. Piping it through `zstd` is not worth it: docker
already stores the layer blobs compressed, so it saves under 0.5%.

## 4. Ship the database and montages

SQLite is in WAL mode, so `scp db/db.sqlite3` silently leaves behind whatever is
still in the `-wal` sidecar. `VACUUM INTO` writes one consistent file, and is
safe even against a running app:

```bash
# [laptop]
rm -f /tmp/melrater-xfer.sqlite3
sqlite3 db/db.sqlite3 "VACUUM INTO '/tmp/melrater-xfer.sqlite3'"

rsync -avz /tmp/melrater-xfer.sqlite3 root@SERVER:/tmp/db.sqlite3
rsync -avz --delete media/ root@SERVER:/tmp/media/          # trailing slash matters
```

Installing it on the server has one non-obvious hazard, so the whole sequence
matters. A `-wal` is found by *path*, not bound to a particular database: if one
is left beside `db.sqlite3` when you replace that file, SQLite replays the old
database's pages into the new one. The result is a site that looks perfectly
healthy — `integrity_check` returns `ok` — while serving the data you thought
you had just overwritten. So: stop the app, keep anything the old sidecar holds,
*then* clear the sidecars and move the file into place.

```bash
# [server]
cd /srv/melrater
docker compose stop melrater 2>/dev/null || docker stop melrater 2>/dev/null || true

# a non-empty -wal means the app died uncleanly and the sidecar holds committed
# server rows; VACUUM INTO folds them back into their OWN database first
[ -s /srv/melrater/db/db.sqlite3-wal ] && \
  sqlite3 /srv/melrater/db/db.sqlite3 "VACUUM INTO '/srv/melrater/backups/pre-push-$(date +%F-%H%M).sqlite3'"

rm -f /srv/melrater/db/db.sqlite3-wal /srv/melrater/db/db.sqlite3-shm
mv /tmp/db.sqlite3 /srv/melrater/db/db.sqlite3
rsync -a --delete /tmp/media/ /srv/melrater/media/ && rm -rf /tmp/media

chown -R 57439:57439 /srv/melrater/db /srv/melrater/media
chmod 750 /srv/melrater/media      # rsync -a copies the source's 0755 over step 2's 0750
```

On a first deploy nothing is running yet, so the stop and the rescue copy are
no-ops — but this is the block a later re-sync re-runs, which is when they earn
their place.

Your local users come with the database — `auth_user` is in that same file — so
there is no `createsuperuser` step. Starting from an empty database instead is
`docker compose run --rm melrater python -m django createsuperuser`, but note
that ingest cannot run here: it needs the bidslake catalog and the raw NIfTIs.

The montages are subject-derived. That should inform where this box lives and
who can reach it.

## 5. Open the firewall [Hetzner console]

Do this before starting, and add the SSH rule *first*: a Cloud Firewall denies
all inbound traffic the moment it is attached, so a firewall carrying only
80/443 rules cuts your own session and leaves the web console as the only way
back in. Outbound is the opposite — leave that tab **completely empty**, since
empty means "allow all"; adding even one outbound rule turns it into a
whitelist and breaks certificate renewal, which needs outbound 443 and DNS.

| Protocol | Port | Source              | Why                                    |
| -------- | ---- | ------------------- | -------------------------------------- |
| TCP      | 22   | your address `/32`  | SSH — add before attaching             |
| TCP      | 80   | `0.0.0.0/0`, `::/0` | ACME `http-01` + the HTTPS redirect     |
| TCP      | 443  | `0.0.0.0/0`, `::/0` | HTTPS                                  |
| UDP      | 443  | `0.0.0.0/0`, `::/0` | HTTP/3; without it browsers use HTTP/2 |
| ICMP     | —    | `0.0.0.0/0`, `::/0` | ping and path-MTU discovery            |

Port 80 has to stay open to the whole internet, not just to you: Let's Encrypt
re-validates on every renewal, which for the short-lived IP certificate below is
roughly every two days. Closing it after the first certificate issues breaks
renewal silently, about 48 hours later.

A `/32` SSH source locks you out when a dynamic address changes. Either use a
range you control, or open 22 broadly and rely on key-only authentication.

## 6. Start it [server]

The quick version, reachable on port 8000 over plain HTTP. Skip it if you are
going straight to HTTPS below:

```bash
docker run -d --name melrater --restart unless-stopped \
  -e MELRATER_DEBUG=0 \
  -e MELRATER_SECRET_KEY="$(openssl rand -base64 48)" \
  -e MELRATER_ALLOWED_HOSTS=2.29.21.207,127.0.0.1 \
  -v /srv/melrater/db:/app/db \
  -v /srv/melrater/media:/app/media \
  -p 8000:8000 \
  psadil/melrater:latest
```

Keep that key: regenerating it on restart logs everyone out. Put it in
`/srv/melrater/.env` (`chmod 600`) rather than in shell history.

`-p 8000:8000` writes its own DNAT rules and is **not** filtered by `ufw` — use
a Hetzner Cloud Firewall for the perimeter, or bind to `-p 127.0.0.1:8000:8000`
and reach it through an SSH tunnel:

```bash
ssh -N -L 8000:127.0.0.1:8000 root@SERVER    # then http://127.0.0.1:8000/
```

### With HTTPS, on the bare IP

Logins and subject-derived images over plain HTTP are worth avoiding, and no
domain is needed: Let's Encrypt has issued certificates for IP addresses since
January 2026, and Caddy (2.11.4 here) will request one.

The compose stack names its container `melrater-melrater-1`, so it will *not*
replace a quick-start container called `melrater` — both would run, and the old
one keeps port 8000. Tear it down first (a silent no-op if it was never
created; nothing durable lives inside it):

```bash
docker rm -f melrater
```

`/srv/melrater/compose.yaml`:

```yaml
name: melrater

services:
  melrater:
    image: psadil/melrater:latest
    restart: unless-stopped
    # no ports: only caddy touches the host network stack
    environment:
      MELRATER_DEBUG: "0"
      MELRATER_SECRET_KEY: ${MELRATER_SECRET_KEY:?set it in /srv/melrater/.env}
      # keep 127.0.0.1 so a healthcheck is not a DisallowedHost; no spaces
      MELRATER_ALLOWED_HOSTS: 2.29.21.207,127.0.0.1
      MELRATER_CSRF_TRUSTED_ORIGINS: https://2.29.21.207
      MELRATER_BEHIND_TLS_PROXY: "1"
    volumes:
      - /srv/melrater/db:/app/db
      - /srv/melrater/media:/app/media
    logging:
      driver: json-file
      options: { max-size: "10m", max-file: "3" }

  caddy:
    image: caddy:2-alpine
    restart: unless-stopped
    ports: ["80:80", "443:443", "443:443/udp"]
    volumes:
      - /srv/melrater/Caddyfile:/etc/caddy/Caddyfile:ro
      - /srv/melrater/caddy/data:/data     # certificates; must persist
      - /srv/melrater/caddy/config:/config
```

`/srv/melrater/Caddyfile`:

```
{
	# Browsers never send SNI when the address bar holds an IP literal (RFC 6066
	# forbids it), so Caddy gets a ClientHello with no server name and cannot
	# pick a certificate: the handshake dies with "tlsv1 alert internal error"
	# and Chrome reports "This site can't provide a secure connection" — even
	# though the log says the certificate was obtained successfully. This names
	# the certificate to serve when no SNI arrives. Not optional for IP hosting.
	default_sni 2.29.21.207

	# Let's Encrypt issues IP certificates only under the short-lived profile:
	# 160 hours (~6 days), renewed automatically. Without this block Caddy asks
	# for an ordinary certificate and Let's Encrypt refuses the IP identifier.
	cert_issuer acme {
		profile shortlived
	}
}

https://2.29.21.207 {
	encode zstd gzip
	reverse_proxy melrater:8000
}
```

The `https://` scheme is doing real work. A bare `2.29.21.207` leaves Caddy to
guess, and an address with no scheme and no certificate ends up served over
plain HTTP. Naming a *domain* here is worse: Caddy turns on automatic HTTPS,
installs a catch-all redirect that sends every request — including ones aimed at
the IP — to `https://`, then has no certificate to present. The symptom is a
`308` on port 80 into a failed TLS handshake on 443, with `could not get
certificate` in the Caddy log.

Because a certificate lives only six days here, a box that stays offline for a
week comes back with an expired one. It recovers on its own once Caddy can
reach Let's Encrypt again, provided port 80 is still open.

`compose` reads `/srv/melrater/.env` for `${MELRATER_SECRET_KEY}` as long as you
run it from that directory:

```bash
cd /srv/melrater
mkdir -p caddy/data caddy/config
printf 'MELRATER_SECRET_KEY=%s\n' "$(openssl rand -base64 48)" > .env && chmod 600 .env
docker compose up -d && docker compose logs -f caddy
```

Watch for `certificate obtained successfully`. Then open:

**<https://2.29.21.207/>**

It redirects to `/accounts/login/`; your existing credentials came across inside
the database. If the browser warns about the certificate, Caddy has not got one
yet — check the Caddy log rather than clicking through, since a warning here
means the connection is not actually protected.

`MELRATER_CSRF_TRUSTED_ORIGINS` and `MELRATER_BEHIND_TLS_PROXY` are what keep
every POST from 403ing: behind a TLS terminator Django sees plain http and
rejects the browser's https `Origin` unless told otherwise. Set
`MELRATER_BEHIND_TLS_PROXY=1` only when the app port is unpublished, as here —
it makes Django trust `X-Forwarded-Proto`, which only the proxy can set.

## Operating it

```bash
docker compose logs -f melrater                                  # or: docker logs -f melrater
docker compose run --rm melrater python -m django createsuperuser
docker compose run --rm melrater python -m django shell
```

Run those from `/srv/melrater`, where the compose file lives. Any management
command works the same way — the entrypoint runs `migrate` and then execs
whatever you passed. Without compose, spell the mounts out:

```bash
docker run --rm -it -e MELRATER_SECRET_KEY=x \
  -v /srv/melrater/db:/app/db -v /srv/melrater/media:/app/media \
  psadil/melrater:latest python -m django createsuperuser
```

Nightly backup, from the host so that it keeps working when the container does
not. `umask 077` matters — the dump is a complete copy of the database, and it
is created root-owned, so the "chown after every sync" habit never covers it:

```bash
umask 077 && sqlite3 /srv/melrater/db/db.sqlite3 "VACUUM INTO '/srv/melrater/backups/db-$(date +%F).sqlite3'"
```

Redeploying a new image, after rebuilding (§1) and shipping it (§3):

```bash
cd /srv/melrater
docker compose pull      # registry path ONLY — fails with "denied" after docker load
docker compose up -d     # recreates the container; migrations run in the entrypoint
docker image prune -f
```

Two lines, not `pull && up -d`: on the `docker save`/`docker load` path the pull
fails and `&&` would stop the deploy from happening at all. On the registry path
the pull is still needed — compose's default `pull_policy` is `missing`, so
`up -d` alone would keep running the image already cached under that tag.

## Sharp edges

- **Direction of travel.** Pushing the laptop's database up overwrites ratings
  made on the server. Once reviewers start using it, the server is the
  authoritative copy and data flows server→laptop. There is no merge path;
  pick a direction before handing out the URL.
- **Never `scp` the SQLite file directly**, and never delete a `db.sqlite3-wal`
  that belongs to the `db.sqlite3` still sitting beside it — a non-empty one
  holds committed rows; `VACUUM INTO` folds them in. But the sidecars left over
  when you *replace* that database must always be deleted, which is why §4 does:
  a `-wal` has no tie to a particular file, so SQLite happily replays the old
  database's pages into the new one. `integrity_check` still says `ok`, so the
  only symptom is a healthy-looking site serving the data you meant to discard.
- **Ownership drift** is the most common failure and the most misleading: reads
  succeed, the site looks fine, and the first rating fails because SQLite cannot
  create `-shm` in a directory it does not own. `chown -R 57439:57439` after
  every sync. The entrypoint checks this at startup and refuses to run.
- **The entrypoint also refuses to start without both mounts.** `settings.py`
  creates `db/` at import time, so a typo'd `-v` would otherwise produce a
  working app whose data dies with the container.
  `MELRATER_ALLOW_EPHEMERAL_STATE=1` overrides it for throwaway tests.
- **`rerender_montages` cannot run on the server** — it reads `Run.path`, a
  laptop path. Re-render locally and rsync `media/`.
- **`python -m django dbshell` fails in the container**: the environment locks
  `libsqlite`, not the `sqlite3` CLI. Use `python -m django shell`, or the
  host's `sqlite3` against the bind mount.
- **`MELRATER_ALLOWED_HOSTS` is split on `,` with no trimming.** A space makes a
  host named `" 2.29.21.207"`, and every request 400s.
- **`MELRATER_DEBUG` defaults to on.** Always set it to `0` explicitly.
- **`/accounts/password_reset/` is public** and will 500: `django.contrib.auth.urls`
  is included wholesale and there is no mail relay. Harmless, but worth
  removing from `config/urls.py` if the URL is ever shared widely.
- **`default_sni` is mandatory for IP hosting.** Omit it and Caddy logs
  `certificate obtained successfully` while every browser fails the handshake —
  the log looks healthy, so this reads as a browser or firewall problem when it
  is neither. `openssl s_client -connect IP:443` (no `-servername`) reproduces
  it; adding `-servername IP` makes it pass, which is the tell.
- **Port 80 must stay open forever.** The short-lived IP certificate
  re-validates via `http-01` every couple of days; closing 80 after the first
  issuance breaks renewal ~48 hours later, not immediately.
- **Never add an outbound firewall rule** unless you mean to whitelist. An empty
  outbound tab allows everything; one rule makes it a whitelist and renewal
  loses its path to Let's Encrypt.
- **arm64 is not buildable** as things stand. If the instance is ever a CAX
  (Ampere), `pixi.toml` needs `linux-aarch64` and a re-lock first.
