# melrater

A web app for reviewing MELODIC ICA components, modeled on
[fsleyes's melodic scene](https://open.oxcin.ox.ac.uk/pages/fsl/fsleyes/fsleyes/userdoc/ic_classification.html)
with two additions:

- **More context per component** — the framewise-displacement (motion)
  timecourse rendered time-aligned under the IC timecourse, and every pyFIX
  classifier metric shown against its distribution across the run's
  components (outliers surfaced first).
- **Integrated multi-reviewer classification** — automated pyFIX verdicts are
  stored as reviewers (a model + threshold, e.g. `UKBiobank @ thr1`) alongside
  human reviewers, each rating components **Signal / Noise / Unknown**.

Brain maps are pre-rendered slice montages (AVIF) over the mean functional,
with axial/coronal/sagittal lightbox switching.

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

`create_rater` makes an ordinary reviewer account and prints a generated
20-character password **once**; there is no way to recover it, only to issue a
new one. Send it over a channel you trust and clear your scrollback. Reviewers
never choose or reset their own password — `/accounts/` routes login and logout
and nothing else, so no password-change or password-reset view is reachable — so
re-issuing is `create_rater <username> --reset`, which makes it your job rather
than a page they can find.

Adding `--ingest` also puts the account in the `ingest` group, which is what
`/api/v1/` checks and is the whole grant: the account is otherwise ordinary —
not staff, not a superuser — so what a leaked push password buys is run ingest
and the reviewing UI, not the admin and not anyone's ratings.

A fresh database has **no superuser**, and none is needed: the app never uses
the admin, and `pixi run manage shell` does anything it could. Run
`pixi run manage createsuperuser` only if you want the browsable interface, and
never for a reviewer — a Django admin can rewrite and delete everyone's
ratings. There is no half-measure to reach for either: `is_staff` on its own
carries no model permissions, so such an account logs in to an empty admin.

## Ingest runs

Ingestion reads a [bidslake](https://github.com/psadil/bidslake) catalog
rather than walking directories — build one over your MELODIC+pyFIX
derivatives with the bidslake CLI (the `feat` adapter teaches it FSL's
layout, and handles trees spread across many roots):

```sh
bidslake index -i /path/to/derivatives --adapter feat -o study.duckdb
```

then hand the catalog to `import_run`:

```sh
pixi run manage import_run study.duckdb
```

Every MELODIC run the catalog knows is ingested (the motion parameters and
per-component variance stats come straight from the catalog's
`feat_motion`/`feat_icstats` tables). Already-ingested runs are
skipped, and a run with missing or ambiguous inputs is reported and skipped
rather than guessed at. `--sub/--ses/--task/--run` narrow the import;
`--base-dir` rebases the catalog's roots when the data moved after indexing.

A few hundred runs is roughly an hour, so a run that fails is reported and the
batch carries on; the exit status is non-zero if anything failed, and
`--stop-on-error` aborts on the first one instead. `--dry-run` reports what
would be ingested without writing anything — worth a few seconds before
committing to the hour.

Montage rendering parallelizes across `--workers` processes (default:
CPUs − 2; a 96-component run takes a few seconds).

## Rate

```sh
pixi run serve
```

This collects static files and starts [granian](https://github.com/emmett-framework/granian)
(async, uvloop) on <http://127.0.0.1:8000/>. Keyboard: `1`/`s` signal, `2`/`u` unknown,
`3`/`n` noise, `←`/`→` prev/next component, `g` jump to an IC number.
Tick the **auto-advance** box (or press `a`) to jump to the next component the
moment a rating lands — which is most of the interaction when a run has ninety-six
of them. The run list filters by subject, session, task or run, hides completed
runs, and offers a resume link to the first component you have not rated.

Human ratings are stored per user; the P(signal) strip in the verdict card
colors each component by its human rating once one exists, so disagreements
with FIX stand out as a color on the wrong side of the threshold line.

## Push runs to the deployment

Ingest needs the raw NIfTIs and the catalog, so it happens on the laptop; once
reviewers are working, **the server's database is the authoritative copy of
their ratings** and must never be overwritten. `push_runs` bridges the two over
the deployment's ingest API — one HTTPS request per run, carrying its rows as
JSON and its montages as a tar:

```sh
pixi run manage push_runs 301 302 --server https://<host> --user <ingest account>
```

It prompts for the password unless `MELRATER_PUSH_PASSWORD` is set, so the
credential need not live in a file. Bad push passwords go through django-axes
like any other login, so five of them lock that (address, username) pair for
fifteen minutes — which is worth knowing before you conclude a push is hanging.

A run is ~288 montages and a couple of megabytes of JSON — roughly 20 MB, a few
seconds. A few hundred runs is a few gigabytes and is throughput-bound, so
expect it to take about as long as an `rsync` of the same bytes would have. It
is safe to interrupt: runs the server already holds with the same montages are
skipped, which makes re-running free,
and the comparison is exact rather than a guess, because a run's montage digest
is derived from the montage bytes and so means the same thing in both
databases.

**Nothing about a human reviewer can travel**, on the first push or the
hundredth: `RunPayload` has no way to express one, and a run the server already
holds is only ever re-rendered — its components and classifications are not
reachable from the endpoint at all. The laptop's `auth_user` table is never
copied either. So there is nothing extra to do once reviewers have started, and
there is no merge path for *ratings* because none is needed — only runs move.

Ingesting and pushing can be one step:

```sh
pixi run manage import_run study.duckdb --push https://<host> --user <ingest account>
```

A failed push there is reported separately from a failed ingest: the runs are
still on the laptop, and `push_runs` will pick them up. `--dry-run` reports what
would be sent; `--new` sends only runs the server has never seen; `--force`
re-sends regardless. A run that fails is reported and the batch carries on,
with a non-zero exit at the end.

**Re-renders travel the same way.** `rerender_montages` cannot run on the
server — it reads `Run.path`, a laptop path, and the source NIfTIs are not
there — so re-render locally and push. The new montages are written to a new
digest directory, the row is pointed at it, and the old set is deleted, so
reviewers pick the change up on their next page load. There is no cache to
invalidate and no revision to remember to carry across.

A push that dies mid-request leaves montages under a uuid no row names.
Invisible rather than broken — that ordering is deliberate, and it is why a
half-finished push never shows a reviewer a screen of missing images. Nothing
reclaims the space automatically; do it when convenient:

```bash
# [server]
docker compose run --rm melrater python -m django prune_orphan_montages --dry-run
```

### When the API is unreachable

The endpoint needs the box up and its certificate valid, and Let's Encrypt
issues this IP a short-lived one (see [Firewall](#firewall-hetzner-console)), so a
box left offline for a week comes back with an expired certificate for a couple
of minutes. `push_runs` verifies TLS and offers no way not to — reaching for
`--insecure` at exactly the moment the connection is genuinely unprotected is
how the push password leaks. Wait for renewal.

If you need to move runs while the API is down, Django's own machinery still
works, because every model carries a natural key:

```bash
# [laptop]
pixi run manage dumpdata core.run core.component core.reviewer core.classification \
  --natural-primary --natural-foreign -o runs.json
tar -C media -cf montages.tar runs/
rsync -av runs.json montages.tar hetzner:/tmp/

# [server] -- read runs.json first: unlike a push, this CAN carry a human
# reviewer, and loading one would overwrite that person's ratings
tar -C /srv/melrater/media -xf /tmp/montages.tar && chown -R 57439:57439 /srv/melrater/media
docker compose run --rm -v /tmp:/incoming:ro melrater python -m django loaddata /incoming/runs.json
```

That last caveat is the reason the API is the normal path: it has no way to
carry a human rating, and this does.

## Development

```sh
pixi run -e dev test        # unit tests
pixi run -e dev test-e2e    # playwright browser tests
pixi run -e dev ruff check .
pixi run -e dev ty check
pixi run check-deploy       # Django's production system checks
```

See [contributing.md](contributing.md) for layout and conventions. The
database (`db/`) and ingested montages (`media/`) contain subject-derived
data and stay untracked.

## Deploying

One container, SQLite, two bind mounts. The image both serves the app and runs
management commands; everything durable lives on the host.

The stack's two config files — `compose.yaml` and `Caddyfile` — live in
[`deploy/`](deploy/) in this repo and are copied to the server by
[`deploy/deploy.sh`](deploy/deploy.sh). Edit them here, never on the box.

### What is durable, and where

`settings.py` computes the database and media paths from `BASE_DIR`, which is
`/app` in the image. Neither is configurable by environment variable, so they are
mount points. Everything under `/srv/melrater` is one of three kinds of thing,
and knowing which is which is what makes a redeploy safe:

| Under `/srv/melrater`       | In the app at | Holds                                        | On redeploy   |
| --------------------------- | ------------- | -------------------------------------------- | ------------- |
| `db/`                       | `/app/db`     | `db.sqlite3` + its `-wal`/`-shm` sidecars     | never touched |
| `media/`                    | `/app/media`  | montages, `runs/<uuid>/<digest>/*.avif`       | never touched |
| `backups/`, `caddy/`        | —             | nightly dumps; Caddy's certificates           | never touched |
| `compose.yaml`, `Caddyfile` | —             | rsynced from `deploy/`                        | overwritten   |
| `.env`                      | —             | written on the box, `chmod 600`               | never touched |

Everything else — the pixi environment, `src/`, the collected static files — is
baked into the image and replaced wholesale on redeploy.

Montage paths are `runs/<Run.uuid>/<Run.montage_digest>/`. The uuid, not the
primary key, because a run pushed into this database is assigned a fresh
integer id and its images have to survive that. The digest, a fingerprint of
the rendered bytes, because it makes a re-render additive: the new set is
written beside the old one and the old one is dropped only once the row points
at the new directory. So no montage URL ever changes what it means, which is
what licenses the one-year `immutable` cache header, and there is no revision
counter for the two machines to disagree about.

`Run.path` does hold the laptop path of the source derivatives, but no view
dereferences it — only re-ingest and `rerender_montages` do, and neither runs
on the server.

### Create and lock down the box

#### Firewall [Hetzner console]

Build it complete before you create the server, and add the SSH rule *first*:
the firewall is attached in the create form below and enforced from the box's
first boot, so a firewall carrying only 80/443 gives you a server you can never
log into — the first `ssh` simply times out, which reads as a failed provision
rather than as a missing rule, and a Hetzner firewall sits outside the VM, so
nothing you could do from inside would help.

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
renewal silently, about 48 hours later. The same six-day certificate life means
a box left offline for a week comes back with an expired one; it recovers by
itself once Caddy can reach Let's Encrypt again, provided 80 is open.

A `/32` SSH source locks you out when a dynamic address changes, so narrow it in
two steps: create the firewall with 22 open to `0.0.0.0/0`, confirm that
`ssh hetzner` works, *then* narrow from a session where a mistake is
instantly visible and instantly revertible. Key-only authentication is on from
first boot either way. The tell months later: SSH that starts timing out is
almost always your own address changing, not a dead box.

#### Create the server [Hetzner console]

The firewall from the previous step is attached *here*, in the create form —
that is the whole reason it exists before the server does. Attached at creation
it is enforced from first boot, so port 22 is never briefly world-open on a
fresh root account.

Before pressing create, decide the address. Hetzner's public IPv4 is a **Primary
IP**: its own resource, with an auto-delete flag that defaults to dying with the
server. Turn that flag off — or create the Primary IP standalone and select it in
the form — and the address survives every future rebuild, which is what makes
"rebuild the box" a zero-edit operation (see
[The `.env` file](#the-env-file-server)). An unassigned Primary IP still bills a
small monthly amount; that stops only when you delete it, not when you detach
it.

Two consequences, neither recoverable later. A Primary IP is **location-bound**
— `whois` on the address names the location it belongs to — so the new server
must be created in that same location or the old address will not appear in the
picker at all. And it cannot be moved
between two *running* servers, which attaching at creation sidesteps. There is no
zero-downtime cutover either way: the site is dark from the moment the old box
stops until [Build and ship](#build-and-ship-laptop) brings the new one up, so
if the old box is still alive, pull its backups down first — nothing on that
disk survives.

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

**CX23, not CAX11.** They sit next to each other in the picker, are identically
specced, and are cents apart — but CAX is Ampere arm64. `pixi.lock` resolves
`osx-arm64` and `linux-64` only; there is no `linux-aarch64`, so an Ampere box
does not run this image slowly, it does not run it at all. Nothing warns you
either: the `arch=$(dpkg --print-architecture)` in the Docker install below
installs a perfectly working arm64 Docker, and the first symptom is an
exec-format error at `docker compose up`. Architecture is fixed at creation and
a rescale cannot cross it.

**40 GB is right, and the disk is a one-way door.** Hetzner rescales only to
plans with an equal or larger drive, so the 80 GB taken "to be safe" is
permanent. The arithmetic on 40: Ubuntu and Docker take ~3 GB; the image is
2.43 GB *unpacked* (the 535 MiB under [Without a registry](#without-a-registry)
is the wire size, a different measurement) and a redeploy holds two of those
until `docker image prune -f`; an incoming push holds one run's tar in `/tmp`
while it is read, which is tens of megabytes. That leaves roughly 16 GB of montages,
a few hundred runs. Skip the external volume for the same reason it is a chore later:
`compose.yaml` bind-mounts absolute paths, so a volume added afterwards has to be
mounted at `/srv/melrater` itself — stack stopped, data moved, everything
re-chowned.

#### SSH access [laptop]

Give the box a name so the rest of this guide is literal rather than a
placeholder. In `~/.ssh/config`, with `<host>` standing throughout for the
box's IPv4 as the console reports it:

```
Host hetzner
    HostName <host>
    User root
    IdentityFile ~/.ssh/hetzner
```

`ssh hetzner` from here on, and `deploy.sh` picks the same name up by default
(`MELRATER_SERVER` overrides it). Resist the temptation to also add a fake
hostname to `/etc/hosts`: it would work for SSH while `MELRATER_ALLOWED_HOSTS`,
`default_sni` and the Caddy site address all still need the literal IP, and
pasting the hostname into one of those yields a silent 400 or a dead TLS
handshake.

If you kept the Primary IP, the address is the same but the machine is not. A
rebuilt box has fresh host keys, and OpenSSH files them under `HostName` — the
IP — so it refuses outright with `REMOTE HOST IDENTIFICATION HAS CHANGED` rather
than prompting. Clear the old key first, and check the new fingerprint against
the one the Hetzner console shows rather than accepting it blind:

```bash
ssh-keygen -R <host>
ssh hetzner
```

Skip it and the next step's `apt-get update` is the first thing to fail —
followed by `deploy.sh`'s config rsync, which is the same failure wearing an
rsync costume.

#### Patch it [server]

A fresh Hetzner image lands with a large backlog of pending updates, and this
box is about to have 80 and 443 open to the internet. `apt-get update` only
refreshes the index — it upgrades nothing — so do both, then reboot:

```bash
apt-get update && apt-get upgrade -y
reboot                     # 5-10 s; your SSH session drops, which is expected
```

Then keep it patched without having to remember. This is safe here precisely
because nothing durable lives inside a container and both services carry
`restart: unless-stopped` — [Verify](#verify) checks exactly that:

```bash
apt-get install -y unattended-upgrades
dpkg-reconfigure -plow unattended-upgrades      # answer yes
```

#### Docker and tooling [server]

```bash
apt-get update && apt-get install -y ca-certificates curl rsync sqlite3 btop
install -m 0755 -d /etc/apt/keyrings
curl -fsSL https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc && chmod a+r /etc/apt/keyrings/docker.asc
echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/ubuntu $(. /etc/os-release && echo "$VERSION_CODENAME") stable" > /etc/apt/sources.list.d/docker.list
apt-get update && apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
```

`sqlite3` is needed on the host for the nightly backup — the container ships
`libsqlite`, not the CLI. `btop` answers "is this box about
to fall over" and costs nothing. The Docker apt line derives the codename off the
box, which is why the image choice above is not a preference.

#### Host directories [server]

The container runs as uid/gid 57439 (`mambauser`), and that account does not
exist here, so chown by number — WAL creates its sidecars *in the directory*, so
the directory itself must be writable, not just the database file:

```bash
mkdir -p /srv/melrater/{db,media,backups,caddy/data,caddy/config}
chown -R 57439:57439 /srv/melrater/db /srv/melrater/media /srv/melrater/backups
chmod 750 /srv/melrater /srv/melrater/{db,media,backups}
```

`backups` needs the same 750 as the rest: the nightly job below writes a
complete copy of the database into it. Leave `caddy/` root-owned: that image
runs as root. There is no `incoming/` — runs arrive over the API, so
nothing is ever staged on the host.

#### The `.env` file [server]

This is the one file created on the box and never overwritten.
[`deploy/env.example`](deploy/env.example) documents the two variables but never
ships — the box only ever receives `compose.yaml` and `Caddyfile` — so write it
directly. Both values are generated rather than typed, the address off the
cloud metadata service, which is the box's own answer to what its public
address is:

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

Keep that key stable — regenerating it invalidates every session and logs
everyone out.

`/hetzner/v1/` is the only metadata route that still answers: the
EC2-compatible `/latest/meta-data/…` aliases Hetzner used to mirror were removed
in August 2026, so a tutorial reaching for that form is now simply wrong.
Somewhere without a metadata service at all, `ip -4 -o addr show scope global`
is the fallback — but read it rather than piping it in blind, since a box on a
private network has more than one global address and only one of them is the one
reviewers type.

That leaves the address hand-typed in exactly one place: `HostName` in
`~/.ssh/config` — how this laptop reaches the box. Everything else derives:
every `ssh` and `rsync` goes through the `hetzner` alias, and `compose.yaml` and
the `Caddyfile` both read this file. That is why the Primary IP is worth
keeping — a new address is that one line plus `ssh-keygen -R`, and nothing else.
The `grep` above is worth the second it costs: `${VAR:?}` catches an *unset*
variable, never a wrong one, and a wrong one fails four silent ways.

### Version-control the deployment [laptop]

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
happen. The server therefore needs no git, no credentials, and no build
context; it holds a finished image, two config files, and the data.

Secrets stay out by construction. `.gitignore` excludes `.env` and `.env.*`, and
the committed template is `deploy/env.example` with no leading dot. The
`${MELRATER_SECRET_KEY:?set it in /srv/melrater/.env}` in `compose.yaml` is the
backstop: a missing `.env` stops the deploy with an actionable message instead
of silently starting the app with a fresh key.

`deploy.sh` refuses to run on a dirty tree — untracked counts — because the
config commit it stamps onto `/srv/melrater/DEPLOYED` would otherwise be a lie:

```bash
git status --porcelain      # must be empty, or deploy.sh exits 1 at step zero
```

### Build and ship [laptop]

```bash
./deploy/deploy.sh
```

That is the whole build-and-ship path, and it starts the stack. Its comments
carry the per-step detail; what follows is only what the commands cannot say. It
refuses a dirty tree, as above, and it prints the laptop's image ID beside the
server's, because `up -d` reports success whether or not it replaced anything.

Two things must be true on this laptop before the first run. `docker login`,
because the push step is a bare `docker push` — Docker Hub repos are public by
default, so the server still needs no credentials to pull. And Docker's
containerd image store must be on (Docker Desktop → Settings → General): the
build combines `--provenance`/`--sbom` with `--load`, and attestations make the
output a manifest list, which the classic docker exporter cannot store.

`--platform=linux/amd64` is not optional: this laptop is arm64, the box is x86,
and `pixi.lock` resolves `osx-arm64` and `linux-64` only, as above. It is also why
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

`certificate obtained successfully`, then **`https://<host>/`** should
redirect to `/accounts/login/` — there is nobody to log in as until the next
step creates an account. A browser
certificate warning means Caddy has not got one yet; read the log rather than
clicking through, since a warning here means the connection is genuinely
unprotected. `MELRATER_CSRF_TRUSTED_ORIGINS` and `MELRATER_BEHIND_TLS_PROXY` are
what keep every POST from 403ing once there is someone to post: behind a TLS
terminator Django sees plain http and rejects the browser's https `Origin` unless
told otherwise. `compose.yaml` explains, next to the `no ports` line it depends
on, why trusting `X-Forwarded-Proto` is safe here.

#### Without a registry

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

### Create the accounts and push the runs

The stack has been running since the deploy, against a database the entrypoint
migrated and nobody has written to. That database stays: accounts are made
here, and runs arrive over the API rather than as a copy of the laptop's
database. There is no direction-of-travel hazard to manage as a result —
nothing this section does can overwrite a rating, on the first run or the
hundredth.

```bash
# [server] one reviewer account per person; each password is printed once
docker compose run --rm melrater python -m django create_rater alice
# ...and one account for the laptop to push as
docker compose run --rm melrater python -m django create_rater laptop --ingest
```

Same commands as [Accounts](#accounts) above, run through the image rather than
through pixi; the `--ingest` grant and the deliberate absence of a superuser are
described there.

Then push everything from the laptop, as
[Push runs to the deployment](#push-runs-to-the-deployment) describes:

```bash
# [laptop]
cd ~/git/neuro/melrater
pixi run manage push_runs --server https://<host> --user laptop
```

The montages are subject-derived. That should inform where this box lives and
who can reach it.

### Verify

The stack has to come back by itself after a reboot, or the unattended upgrades
in [Patch it](#patch-it-server) are a liability rather than a safety net.
`restart: unless-stopped` plus dockerd starting at boot is what provides this —
no systemd unit is involved.
Prove it once:

```bash
ssh hetzner reboot
# wait ~15 s
ssh hetzner 'docker ps --format "{{.Names}}\t{{.Status}}"'
#   expect: melrater-melrater-1   Up ... (healthy)
#           melrater-caddy-1      Up ...
curl -sI https://<host>/ | head -1        # expect 302
```

`(healthy)` rather than a bare `Up` comes from the healthcheck in
`compose.yaml`. This also exercises the thing most likely to be misconfigured:
the certificate survives only because `caddy/data` is a real bind mount.

One thing this sequence has not done: the nightly backup. `/srv/melrater/backups`
exists and is empty. Install the crontab entry under "Operating it" before you
hand out the URL.

### Operating it

```bash
docker compose logs -f melrater
docker compose run --rm melrater python -m django shell
```

Run those from `/srv/melrater`, where the compose file lives. Any management
command works the same way — the entrypoint runs `migrate` and then execs
whatever you passed. That includes the account commands from
[Accounts](#accounts): `create_rater alice`, `create_rater alice --reset`, and
`create_rater laptop --ingest --reset` to rotate the push credential.

Revoke the ingest grant without touching anything else:

```bash
docker compose run --rm melrater python -m django shell -c \
  "from django.contrib.auth.models import User; User.objects.get(username='laptop').groups.clear()"
```

Deleting a reviewer who has rated anything raises `ProtectedError` rather than
quietly cascading their ratings away. If an account really must go, disable it
and leave the rows alone — no admin required:

```bash
docker compose run --rm melrater python -m django shell -c \
  "from django.contrib.auth.models import User; User.objects.filter(username='alice').update(is_active=False)"
```

#### Login throttling

Five failed attempts on the same (address, username) pair lock that pair out for
fifteen minutes; a success resets the counter. This covers `/api/v1/` too — the
ingest endpoint authenticates through `django.contrib.auth`, so a bad push
password is throttled exactly like a bad password on the login form. Locking the *pair* rather than
either half is deliberate — everyone arrives through one Caddy container, so an
address-only lockout would freeze the whole team, and a username-only lockout
would let anyone freeze one reviewer at will. Failures and lockouts are logged
to stderr, so `docker compose logs melrater` shows them.

To clear one by hand:

```bash
docker compose run --rm melrater python -m django axes_reset_username alice
```

#### Caddy config changes

Applying a `Caddyfile` change without recreating the container — worth
preferring, since a recreate is a moment where a box with an expired short-lived
certificate re-enters issuance:

```bash
rsync -av deploy/Caddyfile hetzner:/srv/melrater/        # [laptop]
ssh hetzner 'cd /srv/melrater && docker compose exec caddy caddy reload --config /etc/caddy/Caddyfile'
```

A changed `MELRATER_HOST` is the exception: `reload` re-adapts against the
running process's environment, so that one needs `docker compose up -d`.

#### Backups

From the host, so that it keeps working when the container does not. `umask 077`
matters — the dump is a complete copy of the database, and it is created
root-owned, so the "chown after every sync" habit never covers it:

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

#### Plain HTTP, no proxy (debugging only)

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
keeps port 8000 and the real `db.sqlite3` open for writing, and SQLite has one
writer. `--restart unless-stopped` is
deliberately absent above so it cannot survive the reboot in [Verify](#verify):

```bash
docker rm -f melrater
```

#### Redeploying

```bash
./deploy/deploy.sh
```

The same command as [Build and ship](#build-and-ship-laptop), minus the
one-time preflight. It writes `/srv/melrater/DEPLOYED` with the config commit
and the image ID. Data is never in its path: only `compose.yaml` and the
`Caddyfile` are overwritten.

### Sharp edges

- **Never `scp` the SQLite file directly**, and never delete a `db.sqlite3-wal`
  that belongs to the `db.sqlite3` still sitting beside it — a non-empty one
  holds committed rows; `VACUUM INTO` folds them in. If you ever do replace
  that database, the sidecars left beside it must be deleted first: a `-wal`
  has no tie to a particular file, so SQLite replays the old database's pages
  into the new one, and `integrity_check` still says `ok`.
- **Never `rsync --delete` into `/srv/melrater`.** `db/`, `media/`, `backups/`
  and `caddy/data` share that directory with the two managed files. Sync the
  two files by name, as `deploy.sh` does.
- **Don't add a systemd unit that runs `up -d` on boot** — the obvious move. But
  `restart: unless-stopped` already does that ([Verify](#verify) proves it),
  and a unit would additionally restart a container you stopped *deliberately*.
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
- **`default_sni` is mandatory for IP hosting.** Omit it and Caddy logs
  `certificate obtained successfully` while every browser fails the handshake —
  the log looks healthy, so this reads as a browser or firewall problem when it
  is neither. `openssl s_client -connect IP:443` (no `-servername`) reproduces
  it; adding `-servername IP` makes it pass, which is the tell.
- **A rebuild re-issues the certificate even on the same IP**, because
  `caddy/data` dies with the disk. Let's Encrypt meters certificates per exact
  identifier per week, and an IPv4 address counts as its own registered domain;
  routine renewals are coordinated and exempt, but every start from an empty
  `caddy/data` is a fresh order that counts. Ordinary redeploys are free; a
  rebuild loop inside one week is not. Carrying `/srv/melrater/caddy/data` across
  a rebuild avoids it — move it as root, it holds the ACME account key, and do
  not chown it to 57439.
- **arm64 is not buildable** as things stand — the CX23-not-CAX11 line in
  [Create the server](#create-the-server-hetzner-console) is the moment that
  decision gets made. `pixi.toml` would need `linux-aarch64` and a
  re-lock first.
