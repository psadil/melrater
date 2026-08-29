# Contributing

## Tooling

[pixi](https://pixi.sh) is the only environment manager; everything runs as a
pixi task (never call uv/pip directly). One-time setup:

```sh
pixi install --all
prek install                       # git hooks: ruff, ty, codespell, commit style
pixi run -e dev install-browsers   # chromium for the e2e tests
```

Checks (all enforced by the pre-commit hooks):

```sh
pixi run -e dev test       # unit tests
pixi run -e dev test-e2e   # playwright browser tests
pixi run -e dev ruff check .
pixi run -e dev ty check
pixi run check-deploy      # `django check --deploy`, prod-shaped settings
```

`check-deploy` is a task rather than a shell incantation because pixi's
`[activation.env]` *overrides* the inherited environment: a `MELRATER_DEBUG=0`
typed in front of `pixi run` would be silently ignored. The task sets it in the
task's own env, where it wins.

## Conventions

- **Commits** follow [Conventional Commits](https://www.conventionalcommits.org)
  (enforced at commit-msg).
- **Layout**: all source lives under `src/` (`config/` settings,
  `melrater/core/` the app, `templates/`, `static/`); `tests/` at the root;
  `db/` and `media/` are runtime state and stay untracked (they contain
  subject-derived data — never commit or publish them).
- **Architecture** follows the
  [HackSoft Django StyleGuide](https://github.com/HackSoftware/Django-Styleguide):
  writes in `services.py`, reads in `selectors.py`, views stay thin.
  `api.py` (the [django-ninja](https://django-ninja.dev) ingest endpoints) is
  thin in exactly the same way, and `push.py` is its client — Django-free, so
  its tests need only `httpx.MockTransport`. Domain logic (`melodic.py`,
  `metrics.py`, `montage.py`, `charts.py`, `transfer.py`) is Django-free and
  fully typed. `lake.py` is the one module that talks to a
  [bidslake](https://github.com/psadil/bidslake) catalog — it resolves which
  files belong to which run and hands `melodic.py` plain paths/arrays; a role
  that resolves to anything but exactly one file is reported, never guessed.
  `_lake_models.py` is generated (`pixi run regen-lake-models <catalog>`) so
  queries type-check against the feat adapter's vocabulary — regenerate it,
  don't edit it.
- **Management commands** are
  [django-typer](https://django-typer.readthedocs.io) `TyperCommand`s:
  options come from the annotated `handle` signature, the docstring is the
  help text, and validation raises `typer.BadParameter`.
- **Typing**: checked by ty with no rule overrides. django-stubs (PEP 561
  stubs only, no mypy plugin; dev env) resolves managers, field descriptor
  values, and `request.user`; reverse FK accessors are declared as stub-only
  annotations on the models (`components: "RelatedManager[Component]"` —
  Django ignores un-assigned annotations); views narrow `request.user`
  through `_authed_user`. JSONField payloads have pydantic schemas
  (`schemas.py`), and selectors project ORM rows into those typed models at
  the boundary — add fields to the schema rather than reaching into raw JSON
  or `cast`ing.
- **Tests** are pytest, arrange-act-assert, **one assertion per test**
  (a single composite `assert` on one logical claim is fine). Shared setup
  goes in fixtures. Browser-level behavior belongs in `tests/e2e/`
  (marked `e2e`, excluded from the default run).
- **Frontend** stays thin: state lives on the backend, interactions go
  through htmx (vendored in `src/static/vendor/`), and custom JS is limited
  to keyboard shortcuts and toggling pre-rendered content.
- **Migrations**: after `makemigrations`, convert the generated
  `dependencies`/`operations` lists to tuples (ruff's RUF012 is enforced
  everywhere, and tuples avoid ClassVar override conflicts).
- **Configuration**: every deployment-specific value is a `MELRATER_*`
  environment variable read through django-environ in `config/settings.py`, and
  every default is the one that is safe in public — `DEBUG` is **off** unless
  something opts in. A source checkout opts in via `MELRATER_DEV=1` in
  pixi.toml's `[activation.env]`; that variable exists precisely so
  `MELRATER_DEBUG` stays overridable from the shell.
- **Montage files** are ordinary Django media under `MEDIA_ROOT`: written
  through `default_storage`, served by the login-required view in
  `config/urls.py`. `melrater/core/storage.py` owns nothing but their
  *layout*, `runs/<Run.uuid>/<Run.montage_digest>/ic007_axial.avif`. The uuid
  because a run loaded into another database gets a fresh primary key and its
  images have to survive that; the digest — a fingerprint of the rendered
  bytes — because it makes a re-render additive rather than destructive, which
  is what licenses the `immutable` cache header. Never invent a revision
  counter: two databases agree on a content digest without being told, and a
  counter each side increments on its own would hand out a URL that had
  already served different bytes. `montage.py` stays Django-free and renders
  into a plain temporary directory that `storage.store_directory` ingests.
- **A montage name is rebuilt, never accepted.** Everything arriving from
  outside goes through `montage.parse_montage_name` and back out through
  `montage.montage_name`, from a parsed integer, an `AXES` key, and a format
  sniffed from the bytes — so no string a client chose reaches a storage path.
  Keep that pair adjacent in `montage.py` so they cannot drift, and note the
  `\Z` in the pattern: `$` also matches before a trailing newline.
- **Transferring runs between databases** is the ingest API: `push_runs` sends
  one run per request to `/api/v1/runs`, authenticated as an account in the
  `ingest` group. Montages are stored before the rows that name them, so a
  push that dies leaves invisible orphans (`prune_orphan_montages`) rather
  than a run with broken images; a run the server already holds is only ever
  re-rendered, never rewritten. Human reviewers and classifications have no
  representation in `RunPayload` at all, which is what makes a push unable to
  overwrite ratings. Models still carry `natural_key()` and
  `get_by_natural_key()` so `dumpdata`/`loaddata` remain available; add them
  to any new model that has to travel.
- **Serving**: `pixi run serve` runs granian (async, uvloop, ASGI without
  lifespan) and mounts the collected static files itself; montages under
  `/media/` always go through the login-required Django view.
- **Configuration** additions for the ingest API are `MELRATER_INGEST_*`, and
  `INGEST_ENABLED` follows `DEBUG` — a source checkout has the endpoint, a
  bare `docker run` of the image does not, and `compose.yaml` opts in.
- **SQLite in production**: WAL journal, `IMMEDIATE` transactions
  (see `config/settings.py`); don't add a second database backend. An
  `atomic()` block holds the one write lock for its whole duration, so keep
  slow work out of it — `ingest_run` renders and stores montages *before*
  opening its transaction for exactly this reason.
- **Charts** (`charts.py`) are hand-rolled SVG rendered with `|safe`. Every
  value interpolated into them is a float or a palette constant, and labels are
  used only as dictionary keys; keep it that way, or the templates need
  escaping instead.
- **Django template comments are single-line.** `{# ... #}` spanning two lines
  renders as visible page text; use `{% comment %}` for anything longer.
