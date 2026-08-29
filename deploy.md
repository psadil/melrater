# Deploying melrater

One container, SQLite, two bind mounts. The image both serves the app and runs
management commands; everything durable lives on the host.

The stack's two config files — `compose.yaml` and `Caddyfile` — live in
[`deploy/`](deploy/) in this repo and are copied to the server by
[`deploy/deploy.sh`](deploy/deploy.sh). Edit them here, never on the box.

## What is durable, and where

`settings.py` computes the database and media paths from `BASE_DIR`, which is
`/app` in the image. Neither is configurable by environment variable, so they are
mount points. Everything under `/srv/melrater` is one of three kinds of thing,
and knowing which is which is what makes a redeploy safe:

| Under `/srv/melrater`       | In the app at | Holds                                        | On redeploy   |
| --------------------------- | ------------- | -------------------------------------------- | ------------- |
| `db/`                       | `/app/db`     | `db.sqlite3` + its `-wal`/`-shm` sidecars     | never touched |
| `media/`                    | `/app/media`  | pre-rendered montages, `runs/<uuid>/*.avif`   | never touched |
| `incoming/`                 | `/incoming`   | export bundles waiting to be loaded (§6)      | never touched |
| `backups/`, `caddy/`        | —             | nightly dumps; Caddy's certificates           | never touched |
| `compose.yaml`, `Caddyfile` | —             | rsynced from `deploy/`                        | overwritten   |
| `.env`                      | —             | written on the box, `chmod 600`               | never touched |

Everything else — the pixi environment, `src/`, the collected static files — is
baked into the image and replaced wholesale on redeploy.

Montage paths are derived from `Run.uuid`, not from its primary key, so
nothing absolute is stored in the database *and* a run keeps its images when it
is loaded into another database that has already assigned that integer to
something else. That is what makes §6 possible. `Run.path` does hold the laptop
path of the source derivatives, but no view dereferences it — only re-ingest and
`rerender_montages` do, and neither runs on the server.

Nothing opens those files by path either: montage reads and writes go through
the `montages` entry in `settings.STORAGES` (`melrater/core/storage.py`), so
moving them off this disk and into object storage later is a settings change
rather than a refactor.

## 1. Create and lock down the box

### 1.1 Firewall [Hetzner console]

Build it complete before you create the server, and add the SSH rule *first*: it
is enforced from the box's first boot (§1.2), so a firewall carrying only 80/443
gives you a server you can never log into — the first `ssh` simply times out,
which reads as a failed provision rather than as a missing rule, and a Hetzner
firewall sits outside the VM, so nothing you could do from inside would help.

Outbound is the opposite — leave that tab **completely empty**, since empty
means "allow all"; adding even one outbound rule turns it into a whitelist and
breaks certificate renewal, which needs outbound 443 and DNS.

| Protocol | Port | Source              | Why                                    |
| -------- | ---- | ------------------- | -------------------------------------- |
| TCP      | 22   | `0.0.0.0/0` at first | SSH — narrow it later, see below      |
| TCP      | 80   | `0.0.0.0/0`, `::/0` | ACME `http-01` + the HTTPS redirect     |
| TCP      | 443  | `0.0.0.0/0`, `::/0` | HTTPS                                  |
| UDP      | 443  | `0.0.0.0/0`, `::/0` | HTTP/3; without it browsers use HTTP/2 |
| ICMP     | —    | `0.0.0.0/0`, `::/0` | ping and path-MTU discovery            |

Port 80 has to stay open to the whole internet, not just to you: Let's Encrypt
re-validates on every renewal, which for the short-lived IP certificate below is
roughly every two days. Closing it after the first certificate issues breaks
renewal silently, about 48 hours later.

A `/32` SSH source locks you out when a dynamic address changes, so narrow it in
two steps: create the firewall with 22 open to `0.0.0.0/0`, confirm §1.3's
`ssh hetzner` works, *then* narrow from a session where a mistake is
instantly visible and instantly revertible. Key-only authentication is on from
first boot either way. The tell months later: SSH that starts timing out is
almost always your own address changing, not a dead box.

### 1.2 Create the server [Hetzner console]

The firewall from §1.1 is attached *here*, in the create form — that is the whole
reason it exists before the server does. Attached at creation it is enforced from
first boot, so port 22 is never briefly world-open on a fresh root account.

Before pressing create, decide the address. Hetzner's public IPv4 is a **Primary
IP**: its own resource, with an auto-delete flag that defaults to dying with the
server. Turn that flag off — or create the Primary IP standalone and select it in
the form — and the address survives every future rebuild, which is what makes
"rebuild the box" a zero-edit operation (§1.7). An unassigned Primary IP still
bills a small monthly amount; that stops only when you delete it, not when you
detach it.

Two consequences, neither recoverable later. A Primary IP is **location-bound** —
`2.29.21.207` is in `hel1`, per `whois` — so the new server must be in `hel1` or
the old address will not appear in the picker at all. And it cannot be moved
between two *running* servers, which attaching at creation sidesteps. There is no
zero-downtime cutover either way: the site is dark from the moment the old box
stops until §3 brings the new one up, so if the old box is still alive, pull its
backups down first — nothing on that disk survives.

Check plan names and per-location availability in the console
rather than trusting this table; Hetzner's line-up moves.

| Field      | Value                                  | Why it is not a preference |
| ---------- | -------------------------------------- | -------------------------- |
| Location   | `hel1`                                 | Where the Primary IP lives; see above |
| Image      | Ubuntu 24.04 LTS                       | §1.5 hardcodes the Ubuntu Docker repo and derives only the codename — Debian gives a 404, a non-LTS gives a codename Docker never published |
| Type       | **CX23, not CAX11**                    | 2 vCPU / 4 GB / 40 GB, x86. See below |
| Networking | IPv4 only, IPv6 **off**                | `default_sni` names exactly one certificate, and an IPv4 certificate asserts nothing about a v6 address — a dual-stack box answers correctly on only one of them |
| SSH key    | public half of `~/.ssh/id_ed25519`     | The console cannot add one afterwards, and it means no root password is ever mailed |
| Firewall   | the one from §1.1                      | Attached at creation, as above |
| Volume     | none                                   | See below |
| Name       | `hetzner`                         | Same string as the `Host` alias in §1.3 and `deploy.sh`'s default target |

**CX23, not CAX11.** They sit next to each other in the picker, are identically
specced, and are cents apart — but CAX is Ampere arm64. `pixi.lock` resolves
`osx-arm64` and `linux-64` only; there is no `linux-aarch64`, so an Ampere box
does not run this image slowly, it does not run it at all. Nothing warns you
either: §1.5's `arch=$(dpkg --print-architecture)` installs a perfectly working
arm64 Docker, and the first symptom is an exec-format error at `docker compose
up`. Architecture is fixed at creation and a rescale cannot cross it.

**40 GB is right, and the disk is a one-way door.** Hetzner rescales only to
plans with an equal or larger drive, so the 80 GB taken "to be safe" is
permanent. The arithmetic on 40: Ubuntu and Docker take ~3 GB; the image is
2.43 GB *unpacked* (the 535 MiB in §3.1 is the wire size, a different
measurement) and a redeploy holds two of those until
`docker image prune -f`; §4 stages montages through `/tmp`, so a media sync
transiently needs twice their size. That leaves roughly 16 GB of montages, a few
hundred runs. Skip the external volume for the same reason it is a chore later:
`compose.yaml` bind-mounts absolute paths, so a volume added afterwards has to be
mounted at `/srv/melrater` itself — stack stopped, data moved, everything
re-chowned.

### 1.3 SSH access [laptop]

Give the box a name so the rest of this document is literal rather than a
placeholder. In `~/.ssh/config`:

```
Host hetzner
    HostName 2.29.21.207
    User root
    IdentityFile ~/.ssh/id_ed25519
```

`ssh hetzner` from here on, and `deploy.sh` picks the same name up by
default. Resist the temptation to also add a fake hostname to `/etc/hosts`: it
would work for SSH while `MELRATER_ALLOWED_HOSTS`, `default_sni` and the Caddy
site address all still need the literal IP, and pasting the hostname into one of
those yields a silent 400 or a dead TLS handshake.

If you kept the Primary IP, the address is the same but the machine is not. A
rebuilt box has fresh host keys, and OpenSSH files them under `HostName` — the
IP — so it refuses outright with `REMOTE HOST IDENTIFICATION HAS CHANGED` rather
than prompting. Clear the old key first, and check the new fingerprint against
the one the Hetzner console shows rather than accepting it blind:

```bash
ssh-keygen -R 2.29.21.207
ssh hetzner
```

Skip it and §1.4's `apt-get update` is the first thing to fail — followed by
`deploy.sh`'s config rsync, which is the same failure wearing an rsync costume.

### 1.4 Patch it [server]

A fresh Hetzner image lands with a large backlog of pending updates, and this
box is about to have 80 and 443 open to the internet. `apt-get update` only
refreshes the index — it upgrades nothing — so do both, then reboot:

```bash
apt-get update && apt-get upgrade -y
reboot                     # 5-10 s; your SSH session drops, which is expected
```

Then keep it patched without having to remember. This is safe here precisely
because nothing durable lives inside a container and both services carry
`restart: unless-stopped` — §5 verifies exactly that:

```bash
apt-get install -y unattended-upgrades
dpkg-reconfigure -plow unattended-upgrades      # answer yes
```

### 1.5 Docker and tooling [server]

```bash
apt-get update && apt-get install -y ca-certificates curl rsync sqlite3 btop
install -m 0755 -d /etc/apt/keyrings
curl -fsSL https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc && chmod a+r /etc/apt/keyrings/docker.asc
echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/ubuntu $(. /etc/os-release && echo "$VERSION_CODENAME") stable" > /etc/apt/sources.list.d/docker.list
apt-get update && apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
```

`sqlite3` is needed on the host for the backup and for the database swap in §4 —
the container ships `libsqlite`, not the CLI. `btop` answers "is this box about
to fall over" and costs nothing. The Docker apt line derives the codename off the
box, which is why §1.2's image choice is not a preference.

### 1.6 Host directories [server]

The container runs as uid/gid 57439 (`mambauser`), and that account does not
exist here, so chown by number — WAL creates its sidecars *in the directory*, so
the directory itself must be writable, not just the database file:

```bash
mkdir -p /srv/melrater/{db,media,backups,incoming,caddy/data,caddy/config}
chown -R 57439:57439 /srv/melrater/db /srv/melrater/media /srv/melrater/backups
chmod 750 /srv/melrater /srv/melrater/{db,media,backups,incoming}
```

`backups` needs the same 750 as the rest: the nightly job below writes a
complete copy of the database into it. `incoming` holds export bundles on their
way in (§6) and stays root-owned — the container mounts it read-only. Leave
`caddy/` root-owned too: that image runs as root.

### 1.7 The `.env` file [server]

This is the one file created on the box and never overwritten.
[`deploy/env.example`](deploy/env.example) documents the two variables but never
ships — the box only ever receives `compose.yaml` and `Caddyfile` — so write it
directly:

```bash
cat > /srv/melrater/.env <<EOF
MELRATER_SECRET_KEY=$(openssl rand -base64 48)
MELRATER_HOST=2.29.21.207
EOF
chmod 600 /srv/melrater/.env
grep MELRATER_HOST /srv/melrater/.env    # must match the console exactly
```

Keep that key stable — regenerating it invalidates every session and logs
everyone out.

The address is typed by hand in exactly two places: `HostName` in
`~/.ssh/config` (§1.3 — how this laptop reaches the box) and `MELRATER_HOST`
here (what the app answers to). Everything else derives: every `ssh` and `rsync`
goes through the `hetzner` alias, and `compose.yaml` and the `Caddyfile`
both read this file. That is why §1.2 keeps the Primary IP — a new address is
those two lines plus `ssh-keygen -R`, and nothing else. The `grep` above is worth
the second it costs: `${VAR:?}` catches an *unset* variable, never a wrong one,
and a wrong one fails four silent ways.

## 2. Version-control the deployment [laptop]

```
deploy/
  compose.yaml     the stack: app + caddy, limits, healthcheck, mount paths
  Caddyfile        TLS on the bare IP
  env.example      template; the filled-in .env never leaves the box
  deploy.sh        build → smoke-test → push → rsync config → restart → verify
```

They encode six facts that must agree with each other and with `settings.py` —
`ALLOWED_HOSTS`, `CSRF_TRUSTED_ORIGINS`, `default_sni`, the Caddy site address,
the `reverse_proxy melrater:8000` service name, and the bind-mount paths the
entrypoint enforces. Every disagreement fails quietly: a 400 on every request, a
403 on every POST, or a TLS handshake that dies while the log reports
`certificate obtained successfully`. Hand-typed files that exist in exactly one
place with no diff history are the wrong home for that.

They reach the server by `rsync` of two named files, not `git clone`. Cloning
the repo onto the box would drag `src/`, `tests/` and `pixi.lock` along with it
and invite someone to run `docker build` there — the one thing that must never
happen (§3). The server therefore needs no git, no credentials, and no build
context; it holds a finished image, two config files, and the data.

Secrets stay out by construction. `.gitignore` excludes `.env` and `.env.*`, and
the committed template is `deploy/env.example` with no leading dot. The
`${MELRATER_SECRET_KEY:?set it in /srv/melrater/.env}` in `compose.yaml` is the
backstop: a missing `.env` stops the deploy with an actionable message instead
of silently starting the app with a fresh key.

### Commit them before the first deploy

All four files under `deploy/` are untracked until you do, and `deploy.sh`
refuses to run on a dirty tree — untracked counts — because the config commit it
stamps onto `/srv/melrater/DEPLOYED` would otherwise be a lie:

```bash
git add deploy/ && git commit -m "deploy: version-control the deployed stack"
git status --porcelain      # must be empty, or deploy.sh exits 1 at step zero
```

## 3. Deploy [laptop]

```bash
./deploy/deploy.sh
```

That is the whole build-and-ship path, and it starts the stack. Its comments
carry the per-step detail; what follows is only what the commands cannot say. It
refuses a dirty tree (§2), and it prints the laptop's image ID beside the
server's, because `up -d` reports success whether or not it replaced anything.

Two things must be true on this laptop before the first run. `docker login`,
because the push step is a bare `docker push` — Docker Hub repos are public by
default, so the server still needs no credentials to pull. And Docker's
containerd image store must be on (Docker Desktop → Settings → General): the
build combines `--provenance`/`--sbom` with `--load`, and attestations make the
output a manifest list, which the classic docker exporter cannot store.

`--platform=linux/amd64` is not optional: this laptop is arm64, the box is x86,
and `pixi.lock` resolves `osx-arm64` and `linux-64` only (§1.2). It is also why
the image is **not** built on the server — under emulation the build compiles
bidslake, DuckDB's bundled C++ through a Rust extension, in about six minutes on
a 32 GB laptop, and would thrash or OOM a small shared instance. The box receives
a finished image and never needs a compiler. Afterwards only a source edit
invalidates the last layers.

`mode=max` is the fuller of the two provenance modes — measured against this
build it adds the Dockerfile source, all ten build steps and a third
digest-pinned base image over `mode=min`; both record the git revision and
remote. `--sbom=true` catalogues 132 packages. It also records `--build-arg`
**values**, so never pass a secret that way.

Emulation yields a subtly wrong binary far more readily than a failed build, so
the script's laptop-side import check is not the proof — `(healthy)` is. The
healthcheck loads `/accounts/login/`, which resolves the URLconf, which is what
imports bidslake, on the box's real amd64 CPU. The script's own last line will
still say `(health: starting)`: the first probe does not run until 30 s in.
Watch the certificate arrive while you wait:

```bash
ssh hetzner 'cd /srv/melrater && docker compose logs -f caddy'
```

`certificate obtained successfully`, then **<https://2.29.21.207/>** should
redirect to `/accounts/login/` — there is nobody to log in as until §4. A browser
certificate warning means Caddy has not got one yet; read the log rather than
clicking through, since a warning here means the connection is genuinely
unprotected. `MELRATER_CSRF_TRUSTED_ORIGINS` and `MELRATER_BEHIND_TLS_PROXY` are
what keep every POST from 403ing once there is someone to post: behind a TLS
terminator Django sees plain http and rejects the browser's https `Origin` unless
told otherwise. `compose.yaml` explains, next to the `no ports` line it depends
on, why trusting `X-Forwarded-Proto` is safe here.

### 3.1 Without a registry

`docker save` silently drops attestations, so the registry is the only transport
that carries the provenance and the SBOM to the far end; the SSH pipe delivers a
runnable but unattested image. It moves about 535 MiB, and piping that through
`zstd` is not worth it — docker already stores the layer blobs compressed, so it
saves under 0.5%. The comment above the push in `deploy.sh` spells out the edit:
swap it for `docker save "$TAG" | ssh "$SERVER" 'docker load'`, and **delete**
the `docker compose pull` from the restart step rather than chaining it — it
fails with `denied` when nothing was ever pushed, and the remote block's `set -e`
aborts the deploy there. On the registry path that pull is load-bearing:
compose's default `pull_policy` is `missing`, so `up -d` alone keeps running the
image already cached under that tag.

## 4. Ship the database and montages

The stack has been running since §3 against a database the entrypoint migrated
and nobody has written to. This replaces that file wholesale.

SQLite is in WAL mode, so `scp db/db.sqlite3` silently leaves behind whatever is
still in the `-wal` sidecar. `VACUUM INTO` writes one consistent file, and is
safe even against a running app. Check it fits first — the montages land in
`/tmp` before moving into place, so the box needs about twice their size free:
`du -sh media/` here against `ssh hetzner df -h /`.

```bash
# [laptop]
cd ~/git/neuro/melrater      # db/ and media/ are relative paths, and both are gitignored
rm -f /tmp/melrater-xfer.sqlite3
sqlite3 db/db.sqlite3 "VACUUM INTO '/tmp/melrater-xfer.sqlite3'"

rsync -avze ssh /tmp/melrater-xfer.sqlite3 hetzner:/tmp/db.sqlite3
rsync -avze ssh --delete media/ hetzner:/tmp/media/
```

At a few hundred runs `media/` is ~86,000 files, and rsync pays a round trip per
file. If that push is slow, stream it as one archive instead — and note this is
the *only* time you move media this way; §6 handles everything afterwards:

```bash
tar -C media -cf - . | ssh hetzner 'mkdir -p /tmp/media && tar -C /tmp/media -xf -'
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
  sqlite3 /srv/melrater/db/db.sqlite3 "VACUUM INTO '/srv/melrater/backups/pre-push-$(date +%F-%H%M).sqlite3'" || true

rm -f /srv/melrater/db/db.sqlite3-wal /srv/melrater/db/db.sqlite3-shm
mv /tmp/db.sqlite3 /srv/melrater/db/db.sqlite3
rsync -a --delete /tmp/media/ /srv/melrater/media/ && rm -rf /tmp/media

chown -R 57439:57439 /srv/melrater/db /srv/melrater/media
chmod 750 /srv/melrater/media      # rsync -a copies the source's 0755 over §1.6's 0750

docker compose start melrater      # migrate re-runs, idempotently, against the file you just moved
```

The stack is up from §3, so the stop is real. The rescue copy fires only if the
app did not shut down cleanly — a clean `docker compose stop` checkpoints and
removes the `-wal`, so on a first deploy it does not fire at all. This is the
block a later re-sync re-runs unchanged; only the contents differ.

Your local users come with the database — `auth_user` is in that same file — so
whatever accounts exist on the laptop exist here, and none that do not. If the
laptop database has no superuser, neither does this one; see "Is there an admin
account?" below for whether that matters. Reviewer accounts are made
with `create_rater` (see "Operating it"), on whichever side is authoritative at
the time; after this push, that is the server. Note that ingest cannot run here:
it needs the bidslake catalog and the raw NIfTIs, which is what §6 works around.

The montages are subject-derived. That should inform where this box lives and
who can reach it.

## 5. Verify

The stack has to come back by itself after a reboot, or the unattended upgrades
in §1.4 are a liability rather than a safety net. `restart: unless-stopped` plus
dockerd starting at boot is what provides this — no systemd unit is involved.
Prove it once:

```bash
ssh hetzner reboot
# wait ~15 s
ssh hetzner 'docker ps --format "{{.Names}}\t{{.Status}}"'
#   expect: melrater-melrater-1   Up ... (healthy)
#           melrater-caddy-1      Up ...
curl -sI https://2.29.21.207/ | head -1        # expect 302
```

`(healthy)` rather than a bare `Up` comes from the healthcheck in
`compose.yaml`. This also exercises the thing most likely to be misconfigured:
the certificate survives only because `caddy/data` is a real bind mount.

One thing this sequence has not done: the nightly backup. `/srv/melrater/backups`
exists and is empty. Install the crontab entry under "Operating it" before you
hand out the URL.

## 6. Adding runs after reviewers have started

Section 4 pushes the laptop's whole database up, which **overwrites every rating
made on the server**. That is fine exactly once. After the URL goes out, the
server holds work the laptop has never seen, and run 301 has to arrive without
touching it.

It travels as a Django fixture plus a tar of montages. `export_runs` selects the
runs and, crucially, drops every *human* reviewer and classification, so a
bundle is structurally incapable of overwriting anyone's ratings — only FIX
verdicts ride along. Loading it is stock `loaddata`; there is no custom import
command to go wrong.

```bash
# [laptop] ingest the new runs as usual, then export just those
pixi run manage import_run study.duckdb
pixi run manage export_runs 301 302 303 --out ./outgoing

rsync -av ./outgoing/ hetzner:/srv/melrater/incoming/
```

On a box provisioned before this section existed, create the directory first —
`mkdir -p /srv/melrater/incoming && chmod 750 /srv/melrater/incoming` — and
redeploy so `compose.yaml` picks up the new mount.

Bundles are chunked (25 runs each by default, `--runs-per-bundle`) because
`loaddata` reads a whole fixture into memory inside one transaction, and a few
hundred runs of component timecourses is more than `compose.yaml`'s
`mem_limit: 1500m`.

```bash
# [server]
cd /srv/melrater
for f in incoming/*-media.tar; do tar -xf "$f" -C media/; done
chown -R 57439:57439 media

for f in incoming/runs-*.json; do
  docker compose run --rm melrater python -m django loaddata "/incoming/$(basename "$f")"
done

rm -f incoming/runs-*            # only after the runs show up in the UI
```

Every model carries a natural key, so `loaddata` matches on `Run.uuid` rather
than on a primary key: an interrupted load can simply be re-run, and a bundle
that is already present updates in place instead of duplicating. Montages are
stored under `runs/<uuid>/`, which is why the tar drops straight into `media/`
with no renaming — a loaded run gets a new integer id, and nothing depends on it.

This also replaces §4's file-by-file `rsync` of `media/` for anything after the
first push: a few hundred runs is ~86,000 files, and a handful of tars moves
them in a fraction of the time.

## Operating it

```bash
docker compose logs -f melrater
docker compose run --rm melrater python -m django shell
```

Run those from `/srv/melrater`, where the compose file lives. Any management
command works the same way — the entrypoint runs `migrate` and then execs
whatever you passed.

### Reviewer accounts

```bash
docker compose run --rm melrater python -m django create_rater alice
```

This prints a generated 20-character password **once**. Send it over a channel
you trust and clear your scrollback; there is no reset page to point anyone at,
so re-issuing means `create_rater alice --reset`.

Reviewers are deliberately not superusers, so **do not** use `createsuperuser`
for them: a Django admin can read, rewrite and delete everyone else's ratings,
and delete the accounts that made them.

### Is there an admin account?

Not unless you make one. A database built by the entrypoint's `migrate` has no
superuser, and `create_rater` never creates one, so `/admin/` is a door nobody
can open. That is a reasonable place to stay: the app never needs it, and
`python -m django shell` can do anything the admin can.

Make one only if you want the browsable interface, and keep out of it day to day:

```bash
docker compose run --rm melrater python -m django createsuperuser
```

There is no half-measure to reach for — `is_staff` on its own carries no model
permissions, so such an account logs in to an empty admin.

Deleting a reviewer who has rated anything now raises `ProtectedError` rather
than quietly cascading their ratings away. If an account really must go, disable
it and leave the rows alone — no admin required:

```bash
docker compose run --rm melrater python -m django shell -c \
  "from django.contrib.auth.models import User; User.objects.filter(username='alice').update(is_active=False)"
```

### Login throttling

Five failed attempts on the same (address, username) pair lock that pair out for
fifteen minutes; a success resets the counter. Locking the *pair* rather than
either half is deliberate — everyone arrives through one Caddy container, so an
address-only lockout would freeze the whole team, and a username-only lockout
would let anyone freeze one reviewer at will. Failures and lockouts are logged
to stderr, so `docker compose logs melrater` shows them.

To clear one by hand:

```bash
docker compose run --rm melrater python -m django axes_reset_username alice
```

Applying a `Caddyfile` change without recreating the container — worth
preferring, since a recreate is a moment where a box with an expired short-lived
certificate re-enters issuance:

```bash
rsync -av deploy/Caddyfile hetzner:/srv/melrater/        # [laptop]
ssh hetzner 'cd /srv/melrater && docker compose exec caddy caddy reload --config /etc/caddy/Caddyfile'
```

A changed `MELRATER_HOST` is the exception: `reload` re-adapts against the
running process's environment, so that one needs `docker compose up -d`.

Nightly backup, from the host so that it keeps working when the container does
not. `umask 077` matters — the dump is a complete copy of the database, and it
is created root-owned, so the "chown after every sync" habit never covers it:

```bash
umask 077 && sqlite3 /srv/melrater/db/db.sqlite3 "VACUUM INTO '/srv/melrater/backups/db-$(date +%F).sqlite3'"
```

A command nobody runs is not a backup, so schedule it. In `crontab -e` — note
the escaped `%`, which cron otherwise treats as end-of-command:

```
17 3 * * * umask 077 && /usr/bin/sqlite3 /srv/melrater/db/db.sqlite3 "VACUUM INTO '/srv/melrater/backups/db-$(date +\%F).sqlite3'" && find /srv/melrater/backups -name 'db-*.sqlite3' -mtime +14 -delete
```

Backups on the same disk as the database are not backups. Pull a copy down
periodically:

```bash
rsync -av hetzner:/srv/melrater/backups/ ~/melrater-backups/    # [laptop]
```

### Plain HTTP, no proxy (debugging only)

When the question is "is it the app or is it the TLS", run the image alone on
loopback and tunnel to it. Nothing is exposed and the firewall is not involved —
a bare `-p 8000:8000` would write its own DNAT rules that `ufw` does not filter,
and the Hetzner Cloud Firewall is the only perimeter here.

```bash
docker run -d --name melrater \
  -e MELRATER_DEBUG=0 \
  -e MELRATER_SECRET_KEY="$(openssl rand -base64 48)" \
  -e MELRATER_ALLOWED_HOSTS=127.0.0.1 \
  -v /srv/melrater/db:/app/db \
  -v /srv/melrater/media:/app/media \
  -p 127.0.0.1:8000:8000 \
  psadil/melrater:latest

ssh -N -L 8000:127.0.0.1:8000 hetzner    # [laptop] then http://127.0.0.1:8000/
```

Tear it down before starting the real stack. Compose names its container
`melrater-melrater-1`, so this one is not replaced — both would run, and this one
keeps port 8000 and the real `db.sqlite3` open for writing, which is the
two-writers case §4 spends a page avoiding. `--restart unless-stopped` is
deliberately absent above so it cannot survive §5's reboot:

```bash
docker rm -f melrater
```

### Redeploying

```bash
./deploy/deploy.sh
```

The same command as §3, minus the one-time preflight. It writes
`/srv/melrater/DEPLOYED` with the config commit and the image ID. Data is never
in its path: only `compose.yaml` and the `Caddyfile` are overwritten.

## Sharp edges

- **Direction of travel.** §4 pushes the laptop's database up, which overwrites
  ratings made on the server. Do that exactly once, before the URL goes out.
  From then on the server is the authoritative copy: new runs go up through §6,
  which cannot carry a human rating, and everything else flows server→laptop.
  There is still no merge path for *ratings* — only for runs.
- **Never `scp` the SQLite file directly**, and never delete a `db.sqlite3-wal`
  that belongs to the `db.sqlite3` still sitting beside it — a non-empty one
  holds committed rows; `VACUUM INTO` folds them in. But the sidecars left over
  when you *replace* that database must always be deleted, which is why §4 does:
  a `-wal` has no tie to a particular file, so SQLite replays the old database's
  pages into the new one, and `integrity_check` still says `ok`.
- **Never `rsync --delete` into `/srv/melrater`.** `db/`, `media/`, `backups/`,
  `incoming/` and `caddy/data` share that directory with the two managed files.
  Sync the two files by name, as `deploy.sh` does. (`rsync` *into*
  `/srv/melrater/incoming/` is fine — that is what §6 does.)
- **Don't add a systemd unit that runs `up -d` on boot** — the obvious move. But
  `restart: unless-stopped` already does that (§5 proves it), and a unit would
  additionally restart a container you stopped *deliberately* — including
  mid-way through §4, reopening `db.sqlite3` between the sidecar deletion and
  the `mv`.
- **Two substitution syntaxes read the same `.env`.** compose expands
  `${MELRATER_HOST}`; the `Caddyfile` uses `{$MELRATER_HOST}`, expanded by
  Caddy's own adapter from the caddy container's environment — different
  mechanisms, which is why a changed host needs `up -d` and not `reload`.
- **Ownership drift** is the most common failure and the most misleading: reads
  succeed, the site looks fine, and the first rating fails because SQLite cannot
  create `-shm` in a directory it does not own. `chown -R 57439:57439` after
  every sync. The entrypoint checks this at startup and refuses to run.
- **The entrypoint also refuses to start without both mounts.** `settings.py`
  creates `db/` at import time, so a typo'd `-v` would otherwise produce a
  working app whose data dies with the container.
  `MELRATER_ALLOW_EPHEMERAL_STATE=1` overrides it for throwaway tests.
- **`rerender_montages` cannot run on the server** — it reads `Run.path`, a
  laptop path, and the source NIfTIs are not here. Re-render locally, then ship
  the result through §6: a re-render bumps `Run.montage_rev`, every montage URL
  carries it as `?v=`, and the media view answers with a one-year immutable
  cache header — so a re-render that reaches the server without its new
  `montage_rev` leaves reviewers looking at cached old images.
- **`python -m django dbshell` fails in the container**: the environment locks
  `libsqlite`, not the `sqlite3` CLI. Use `python -m django shell`, or the
  host's `sqlite3` against the bind mount.
- **`MELRATER_ALLOWED_HOSTS` is split on `,` with no trimming.** A space makes a
  host named `" 127.0.0.1"`, and every request 400s. This is why `compose.yaml`
  writes `${MELRATER_HOST},127.0.0.1` closed up.
- **`MELRATER_DEBUG` defaults to off**, and `compose.yaml` sets it to `0`
  anyway. The one thing that turns it on is `MELRATER_DEV=1`, which pixi's
  `[activation.env]` sets for a source checkout and which cannot reach this
  image — pixi is not installed on it.
- **`/accounts/` routes login and logout and nothing else.** No password-change
  or password-reset view is reachable, deliberately: passwords are issued by
  `create_rater` and rotated with `create_rater --reset`. If a reviewer needs a
  new one, that is your job, not a page they can find.
- **`create_rater` prints the password once.** There is no way to recover it —
  only to issue a new one.
- **`default_sni` is mandatory for IP hosting.** Omit it and Caddy logs
  `certificate obtained successfully` while every browser fails the handshake —
  the log looks healthy, so this reads as a browser or firewall problem when it
  is neither. `openssl s_client -connect IP:443` (no `-servername`) reproduces
  it; adding `-servername IP` makes it pass, which is the tell.
- **Port 80 must stay open forever.** The short-lived IP certificate
  re-validates via `http-01` every couple of days; closing 80 after the first
  issuance breaks renewal ~48 hours later, not immediately. The same six-day life
  means a box left offline for a week comes back with an expired certificate — it
  recovers by itself once Caddy can reach Let's Encrypt again, provided 80 is
  open.
- **Never add an outbound firewall rule** unless you mean to whitelist. An empty
  outbound tab allows everything; one rule makes it a whitelist and renewal
  loses its path to Let's Encrypt.
- **A rebuild re-issues the certificate even on the same IP**, because
  `caddy/data` dies with the disk. Let's Encrypt meters certificates per exact
  identifier per week, and an IPv4 address counts as its own registered domain;
  routine renewals are coordinated and exempt, but every start from an empty
  `caddy/data` is a fresh order that counts. Ordinary redeploys are free; a
  rebuild loop inside one week is not. Carrying `/srv/melrater/caddy/data` across
  a rebuild avoids it — move it as root, it holds the ACME account key, and do
  not chown it to 57439.
- **arm64 is not buildable** as things stand — §1.2's CX23-not-CAX11 line is the
  moment that decision gets made. `pixi.toml` would need `linux-aarch64` and a
  re-lock first.
