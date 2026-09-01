# melrater

A web app for reviewing MELODIC ICA components, modeled on [fsleyes's melodic scene](https://open.oxcin.ox.ac.uk/pages/fsl/fsleyes/fsleyes/userdoc/ic_classification.html) with two additions:

- **More context per component** — the framewise-displacement (motion) timecourse rendered time-aligned under the IC timecourse, and every pyFIX classifier metric shown against its distribution across the run's components (outliers surfaced first).
- **Integrated multi-reviewer classification** — automated pyFIX verdicts are stored as reviewers (a model + threshold, e.g. `UKBiobank @ thr1`) alongside human reviewers.

## Setup

[pixi](https://pixi.sh) manages everything:

```sh
pixi install --all
pixi run manage migrate
pixi run manage create_rater <username>   # prints a generated password, once
```

Enable the git hooks (ruff, ty, codespell, Conventional Commits) once:

```sh
prek install
```

### Accounts

`create_rater` makes an ordinary reviewer account and prints a generated 20-character password. There is no way to recover it, only to issue a new one.

Adding `--ingest` puts the account in the `ingest` group, which enables the user to upload new images to be rate.

## Ingest runs

Ingestion reads a [bidslake](https://github.com/psadil/bidslake) catalog. Build one over your MELODIC+pyFIX derivatives with the bidslake CLI (the `feat` adapter teaches it FSL's layout, and handles trees spread across many roots):

```sh
bidslake index -i /path/to/derivatives --adapter feat -o study.duckdb
```

then hand the catalog to `import_run`:

```sh
pixi run manage import_run study.duckdb
```

Every MELODIC run the catalog knows is ingested (the motion parameters and per-component variance stats come straight from the catalog's `feat_motion`/`feat_icstats` tables). Already-ingested runs are skipped, and a run with missing or ambiguous inputs is reported and skipped. `--sub/--ses/--task/--run` narrow the import; `--base-dir` rebases the catalog's roots when the data moved after indexing.

A few hundred runs is roughly an hour, so a run that fails is reported and the batch carries on; the exit status is non-zero if anything failed, and `--stop-on-error` aborts on the first one instead. `--dry-run` reports what would be ingested without writing anything — worth a few seconds before committing to the hour.

Montage rendering parallelizes across `--workers` processes (default: CPUs − 2; a 96-component run takes a few seconds).

## Rate

```sh
pixi run serve
```

This collects static files and starts [granian](https://github.com/emmett-framework/granian) on <http://127.0.0.1:8000/>. The run list filters by subject, session, task or run, hides completed runs, and offers a resume link to the first component you have not rated.

## Push runs to the deployment

Ingest needs the raw NIfTIs and the catalog, so it happens locally. Once reviewers are working, the server's database is the authoritative copy of their ratings and must never be overwritten. `push_runs` bridges the two over the deployment's ingest API -- one HTTPS request per run, carrying its rows as
JSON and its montages as a tar:

```sh
pixi run manage push_runs 301 302 --server https://<host> --user <ingest account>
```

It prompts for the password unless `MELRATER_PUSH_PASSWORD` is set, so the credential need not live in a file. Bad push passwords go through django-axes (subject to lock-out after too many failed attempts).

A run is ~288 montages and a couple of megabytes of JSON. A few hundred runs is a few gigabytes and is throughput-bound, so expect it to take about as long as `rsync`. It is safe to interrupt; runs the server already holds with the same montages are skipped, which makes re-running free, and the comparison is exact rather than a guess, because a run's montage digest is derived from the montage bytes and so means the same thing in both databases.

Ingesting and pushing can be one step:

```sh
pixi run manage import_run study.duckdb --push https://<host> --user <ingest account>
```

A failed push there is reported separately from a failed ingest: the runs are still on the laptop, and `push_runs` will pick them up. `--dry-run` reports what would be sent; `--new` sends only runs the server has never seen; `--force` re-sends regardless. A run that fails is reported and the batch carries on, with a non-zero exit at the end.

Re-renders travel the same way. `rerender_montages` cannot run on the server (no niftis there), so re-render locally and push. The new montages are written to a new digest directory, the row is pointed at it, and the old set is deleted, so reviewers pick the change up on their next page load.

A push that dies mid-request leaves montages under a uuid no row names. Invisible rather than broken — that ordering is deliberate, and it is why a half-finished push never shows a reviewer a screen of missing images. Nothing reclaims the space automatically; do it when convenient:

```bash
# [server]
docker compose run --rm melrater python -m django prune_orphan_montages --dry-run
```

## Development

```sh
pixi run -e dev test        # unit tests
pixi run -e dev test-e2e    # playwright browser tests
pixi run -e dev ruff check .
pixi run -e dev ty check
pixi run check-deploy       # Django's production system checks
```

See [contributing.md](contributing.md) for layout and conventions. The database (`db/`) and ingested montages (`media/`) contain subject-derived data and stay untracked.

## Deploying

One container, SQLite, two bind mounts. The image both serves the app and runs management commands; everything durable lives on the host.

The stack's one config file — `compose.yaml` — lives in [`deploy/`](deploy/) in this repo and is copied to the server by [`deploy/deploy.sh`](deploy/deploy.sh). Edit it here, never on the box. TLS termination and routing live in the shared `proxy` repo (deployed at `/srv/proxy`), whose caddy owns ports 80/443 and routes this app at the site root — and dirt under `/dirt/` — over the external `proxy` docker network.

### What is durable, and where

`settings.py` computes the database and media paths from `BASE_DIR`, which is `/app` in the image. Neither is configurable by environment variable, so they are mount points. Everything under `/srv/melrater` is one of three kinds of thing, and knowing which is which is what makes a redeploy safe:

| Under `/srv/melrater`       | In the app at | Holds                                        | On redeploy   |
| --------------------------- | ------------- | -------------------------------------------- | ------------- |
| `db/`                       | `/app/db`     | `db.sqlite3` + its `-wal`/`-shm` sidecars     | never touched |
| `media/`                    | `/app/media`  | montages, `runs/<uuid>/<digest>/*.avif`       | never touched |
| `backups/`                  | —             | nightly dumps                                 | never touched |
| `compose.yaml`              | —             | rsynced from `deploy/`                        | overwritten   |
| `.env`                      | —             | written on the box, `chmod 600`               | never touched |

Everything else (the pixi environment, `src/`, the collected static files) is baked into the image and replaced wholesale on redeploy.

Montage paths are `runs/<Run.uuid>/<Run.montage_digest>/`. The uuid, not the primary key, because a run pushed into this database is assigned a fresh integer id and its images have to survive that. The digest, a fingerprint of the rendered bytes, because it makes a re-render additive: the new set is written beside the old one and the old one is dropped only once the row points at the new directory. So no montage URL ever changes what it means, which is what licenses the one-year `immutable` cache header, and there is no revision counter for the two machines to disagree about.

`Run.path` does hold the laptop path of the source derivatives, but no view dereferences it — only re-ingest and `rerender_montages` do, and neither runs on the server.

### Create and lock down the box

#### Firewall [Hetzner console]

Build it complete before you create the server, and add the SSH rule first: the firewall is attached in the create form below and enforced from the box's first boot, so a firewall carrying only 80/443 gives you a server you can never log into — the first `ssh` simply times out, which reads as a failed provision rather than as a missing rule, and a Hetzner firewall sits outside the VM, so nothing you could do from inside would help. Leave the outbound tap empty.

| Protocol | Port | Source              | Why                                    |
| -------- | ---- | ------------------- | -------------------------------------- |
| TCP      | 22   | `0.0.0.0/0` at first | SSH — narrow it later, see below      |
| TCP      | 80   | `0.0.0.0/0`, `::/0` | ACME `http-01` + the HTTPS redirect     |
| TCP      | 443  | `0.0.0.0/0`, `::/0` | HTTPS                                  |
| UDP      | 443  | `0.0.0.0/0`, `::/0` | HTTP/3; without it browsers use HTTP/2 |
| ICMP     | —    | `0.0.0.0/0`, `::/0` | ping and path-MTU discovery            |

Port 80 has to stay open to the whole internet, not just to you: Let's Encrypt re-validates on every renewal, which for the short-lived IP certificate below is roughly every two days. Closing it after the first certificate issues breaks renewal silently, about 48 hours later. The same six-day certificate life means a box left offline for a week comes back with an expired one; it recovers by itself once Caddy can reach Let's Encrypt again, provided 80 is open.

A `/32` SSH source locks you out when a dynamic address changes, so narrow it in two steps: create the firewall with 22 open to `0.0.0.0/0`, confirm that `ssh hetzner` works, then narrow from a session where a mistake is instantly visible and instantly revertible. Key-only authentication is on from first boot either way. The tell months later: SSH that starts timing out is almost always your own address changing, not a dead box.

#### Create the server [Hetzner console]

The firewall from the previous step is attached here, in the create form. Attached at creation it is enforced from first boot, so port 22 is never briefly world-open on a fresh root account.

Before pressing create, decide the address. Hetzner's public IPv4 is a Primary IP: its own resource, with an auto-delete flag that defaults to dying with the server. Turn that flag off (or create the Primary IP standalone and select it in the form) and the address survives every future rebuild, which is what makes "rebuild the box" a zero-edit operation (see [The `.env` file](#the-env-file-server)). An unassigned Primary IP still bills a small monthly amount; that stops only when you delete it, not when you detach it.

Two consequences, neither recoverable later. A Primary IP is location-bound; `whois` on the address names the location it belongs to, so the new server must be created in that same location or the old address will not appear in the picker at all. And it cannot be moved between two running servers, which attaching at creation sidesteps.

Check plan names and per-location availability in the console
rather than trusting this table; Hetzner's line-up moves.

| Field      | Value                                  | Why it is not a preference |
| ---------- | -------------------------------------- | -------------------------- |
| Location   | `hel1`                                 | Where the Primary IP lives; see above |
| Image      | Ubuntu 24.04 LTS                       | the Docker install below hardcodes the Ubuntu repo and derives only the codename — Debian gives a 404, a non-LTS gives a codename Docker never published |
| Type       | **CX23, not CAX11**                    | 2 vCPU / 4 GB / 40 GB, x86. See below |
| Networking | IPv4 only, IPv6 **off**                | `default_sni` names exactly one certificate, and an IPv4 certificate asserts nothing about a v6 address — a dual-stack box answers correctly on only one of them |
| SSH key    | public half of the key in [SSH access](#ssh-access-laptop) | The console cannot add one afterwards, and it means no root password is ever mailed |
| Firewall   | the one from [Firewall](#firewall-hetzner-console) | Attached at creation, as above |
| Name       | `hetzner`                              | Same string as the `Host` alias in [SSH access](#ssh-access-laptop), which is `deploy.sh`'s default target |
| Volume     | none                                   | See below |

40 GB is likely enough. The arithmetic on 40: Ubuntu and Docker take ~3 GB; the image is 2.43 GB *unpacked* (the 535 MiB under [Without a registry](#without-a-registry) is the wire size, a different measurement) and a redeploy holds two of those until `docker image prune -f`; an incoming push holds one run's tar in `/tmp` while it is read, which is tens of megabytes. That leaves roughly 16 GB of montages, a few hundred runs. Skip the external volume for the same reason it is a chore later: `compose.yaml` bind-mounts absolute paths, so a volume added afterwards has to be mounted at `/srv/melrater` itself — stack stopped, data moved, everything re-chowned.

#### SSH access [laptop]

Give the box a name so the rest of this guide is literal rather than a placeholder. In `~/.ssh/config`, with `<host>` standing throughout for the box's IPv4 as the console reports it:

```
Host hetzner
    HostName <host>
    User root
    IdentityFile ~/.ssh/hetzner
```

`ssh hetzner` from here on, and `deploy.sh` picks the same name up by default (`MELRATER_SERVER` overrides it). Resist the temptation to also add a fake hostname to `/etc/hosts`: it would work for SSH while `MELRATER_ALLOWED_HOSTS`, `default_sni` and the Caddy site address all still need the literal IP, and pasting the hostname into one of those yields a silent 400 or a dead TLS handshake.

If you kept the Primary IP, the address is the same but the machine is not. A rebuilt box has fresh host keys, and OpenSSH files them under `HostName` — the IP — so it refuses outright with `REMOTE HOST IDENTIFICATION HAS CHANGED` rather than prompting. Clear the old key first, and check the new fingerprint against the one the Hetzner console shows rather than accepting it blind:

```bash
ssh-keygen -R <host>
ssh hetzner
```

Skip it and the next step's `apt-get update` is the first thing to fail — followed by `deploy.sh`'s config rsync, which is the same failure wearing an rsync costume.

#### Docker and tooling [server]

[Follow the `apt` instructions](https://docs.docker.com/engine/install/ubuntu/#install-using-the-repository).

A few other helpful tools include `sqlite3`, `btop`, `curl`, `rsync`.

#### Host directories [server]

The container runs as uid/gid 57439 (`mambauser`), and that account does not exist here, so chown by number — WAL creates its sidecars in the directory, so the directory itself must be writable, not just the database file:

```bash
mkdir -p /srv/melrater/{db,media,backups}
chown -R 57439:57439 /srv/melrater/db /srv/melrater/media /srv/melrater/backups
chmod 750 /srv/melrater /srv/melrater/{db,media,backups}
```

`backups` needs the same 750 as the rest: the nightly job below writes a complete copy of the database into it. (Caddy's certificate state lives under `/srv/proxy`, owned by the proxy repo.)

#### The `.env` file [server]

This is the one file created on the box and never overwritten. [`deploy/env.example`](deploy/env.example) documents the two variables but never ships — the box only ever receives `compose.yaml` — so write it directly. Both values are generated rather than typed, the address off the cloud metadata service, which is the box's own answer to what its public address is:

```bash
HOST=$(curl -fsS http://169.254.169.254/hetzner/v1/metadata/public-ipv4)
: "${HOST:?no address from the metadata service — read it off the console}"
cat > /srv/melrater/.env <<EOF
MELRATER_SECRET_KEY=$(openssl rand -base64 48)
MELRATER_HOST=$HOST
EOF
chmod 600 /srv/melrater/.env
grep MELRATER_HOST /srv/melrater/.env    # must match the console exactly
```

Again, keep that key stable. Regenerating it invalidates every session and logs everyone out.

That leaves the address hand-typed in exactly one place: `HostName` in `~/.ssh/config` — how this laptop reaches the box. Everything else derives: every `ssh` and `rsync` goes through the `hetzner` alias, and `compose.yaml` reads this file (the proxy stack keeps the same address in its own `/srv/proxy/.env`). That is why the Primary IP is worth keeping — a new address is that one line plus `ssh-keygen -R`, and nothing else. The `grep` above is worth the second it costs: `${VAR:?}` catches an unset variable, never a wrong one, and a wrong one fails four silent ways.

### Version-control the deployment [laptop]

```
deploy/
  compose.yaml     the stack: the app, its limits, healthcheck, mount paths
  env.example      template; the filled-in .env never leaves the box
  deploy.sh        build → smoke-test → push → rsync config → restart → verify

(TLS on the bare IP — the Caddyfile — lives in the proxy repo.)
```

They encode the facts that must agree with each other and with `settings.py` — `ALLOWED_HOSTS`, `CSRF_TRUSTED_ORIGINS`, and the bind-mount paths the entrypoint enforces — while the proxy repo holds the other half: `default_sni`, the site address, and the `reverse_proxy melrater:8000` service name this compose project provides on the shared network. Every disagreement fails quietly: a 400 on every request, a 403 on every POST, or a TLS handshake that dies while the log reports `certificate obtained successfully`. Hand-typed files that exist in exactly one place with no diff history are the wrong home for that.

They reach the server by `rsync` of one named file, not `git clone`. Cloning the repo onto the box would drag `src/`, `tests/` and `pixi.lock` along with it and invite someone to run `docker build` there. The server therefore needs no git, no credentials, and no build context; it holds a finished image, the config files, and the data.

### Build and ship [laptop]

```bash
./deploy/deploy.sh
```

The healthcheck loads `/accounts/login/`, which resolves the URLconf, which is what imports bidslake, on the box's real amd64 CPU. The script's own last line will still say `(health: starting)`: the first probe does not run until 30 s in. Watch the certificate arrive while you wait:

```bash
ssh hetzner 'cd /srv/proxy && docker compose logs -f caddy'
```

`certificate obtained successfully`, then `https://<host>/` should redirect to `/accounts/login/` — there is nobody to log in as until the next step creates an account. A browser certificate warning means Caddy has not got one yet; read the log rather than clicking through, since a warning here means the connection is genuinely unprotected. `MELRATER_CSRF_TRUSTED_ORIGINS` and `MELRATER_BEHIND_TLS_PROXY` are what keep every POST from 403ing once there is someone to post: behind a TLS terminator Django sees plain http and rejects the browser's https `Origin` unless told otherwise. `compose.yaml` explains, next to the `no ports` line it depends on, why trusting `X-Forwarded-Proto` is safe here.

### Create the accounts and push the runs

The stack has been running since the deploy, against a database the entrypoint migrated and nobody has written to. That database stays: accounts are made here, and runs arrive over the API rather than as a copy of the laptop's database. There is no direction-of-travel hazard to manage as a result.

```bash
# [server] one reviewer account per person; each password is printed once
docker compose run --rm melrater python -m django create_rater alice
# ...and one account for the laptop to push as
docker compose run --rm melrater python -m django create_rater laptop --ingest
```

Same commands as [Accounts](#accounts) above, run through the image rather than through pixi; the `--ingest` grant and the deliberate absence of a superuser are described there.

Then push everything from the laptop, as [Push runs to the deployment](#push-runs-to-the-deployment) describes:

```bash
# [laptop]
cd ~/git/neuro/melrater
pixi run manage push_runs --server https://<host> --user laptop
```

The montages are subject-derived. That should inform where this box lives and who can reach it.

#### Login throttling

Five failed attempts on the same (address, username) pair lock that pair out for fifteen minutes; a success resets the counter. This covers `/api/v1/` too — the ingest endpoint authenticates through `django.contrib.auth`, so a bad push password is throttled exactly like a bad password on the login form. Locking the pair rather than either half is deliberate — everyone arrives through one Caddy container, so an address-only lockout would freeze the whole team, and a username-only lockout would let anyone freeze one reviewer at will. Failures and lockouts are logged to stderr, so `docker compose logs melrater` shows them.

To clear one by hand:

```bash
docker compose run --rm melrater python -m django axes_reset_username alice
```

#### Redeploying

```bash
./deploy/deploy.sh
```

The same command as [Build and ship](#build-and-ship-laptop), minus the one-time preflight. It writes `/srv/melrater/DEPLOYED` with the config commit and the image ID. Data is never in its path: only `compose.yaml` is overwritten.

### Sharp edges

- **Never `scp` the SQLite file directly**, and never delete a `db.sqlite3-wal` that belongs to the `db.sqlite3` still sitting beside it — a non-empty one holds committed rows; `VACUUM INTO` folds them in. If you ever do replace that database, the sidecars left beside it must be deleted first: a `-wal` has no tie to a particular file, so SQLite replays the old database's pages into the new one, and `integrity_check` still says `ok`.
- **Never `rsync --delete` into `/srv/melrater`.** `db/`, `media/` and `backups/` share that directory with the managed file. Sync it by name, as `deploy.sh` does.
- **Two substitution syntaxes read the boxes' `.env` files.** compose expands `${MELRATER_HOST}`; the proxy repo's `Caddyfile` uses `{$PROXY_HOST}`, expanded by Caddy's own adapter from that container's environment — different mechanisms, which is why a changed host needs `up -d` and not `reload`.
- **Ownership drift** is the most common failure and the most misleading: reads succeed, the site looks fine, and the first rating fails because SQLite cannot create `-shm` in a directory it does not own. `chown -R 57439:57439` after every sync. The entrypoint checks this at startup and refuses to run.
- **`python -m django dbshell` fails in the container**: the environment locks `libsqlite`, not the `sqlite3` CLI. Use `python -m django shell`, or the host's `sqlite3` against the bind mount.
- **`MELRATER_ALLOWED_HOSTS` is split on `,` with no trimming.** A space makes a host named `" 127.0.0.1"`, and every request 400s. This is why `compose.yaml` writes `${MELRATER_HOST},127.0.0.1` closed up.
- **`MELRATER_DEBUG` defaults to off**, and `compose.yaml` sets it to `0` anyway. The one thing that turns it on is `MELRATER_DEV=1`, which pixi's `[activation.env]` sets for a source checkout and which cannot reach this image — pixi is not installed on it.
- **`default_sni` is mandatory for IP hosting** (set in the proxy repo's `Caddyfile`). Omit it and Caddy logs `certificate obtained successfully` while every browser fails the handshake — the log looks healthy, so this reads as a browser or firewall problem when it is neither. `openssl s_client -connect IP:443` (no `-servername`) reproduces it; adding `-servername IP` makes it pass, which is the tell.
- **A rebuild re-issues the certificate even on the same IP**, because `caddy/data` dies with the disk. Let's Encrypt meters certificates per exact identifier per week, and an IPv4 address counts as its own registered domain; routine renewals are coordinated and exempt, but every start from an empty `caddy/data` is a fresh order that counts. Ordinary redeploys are free; a rebuild loop inside one week is not. Carrying `/srv/proxy/caddy/data` across a rebuild avoids it — move it as root, it holds the ACME account key, and do not chown it to 57439.
