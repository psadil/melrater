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
  Domain logic (`melodic.py`, `metrics.py`, `montage.py`, `charts.py`) is
  Django-free and fully typed. `lake.py` is the one module that talks to a
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
- **Montage files** are reached only through the `montages` entry in
  `settings.STORAGES`, via `melrater/core/storage.py` — never through
  `MEDIA_ROOT` directly. Keeping that seam is what would let ~6 GB of images
  move to object storage as a settings change. They are keyed by `Run.uuid`,
  not by primary key, so a run stays intact when it is loaded into another
  database (see below). `montage.py` stays Django-free and renders into a plain
  temporary directory that `storage.store_directory` then ingests.
- **Transferring runs between databases** uses Django's own machinery: every
  model defines `natural_key()` and a manager with `get_by_natural_key()`, and
  `export_runs` writes fixtures with `use_natural_primary_keys` so the far side
  is plain `loaddata`. Human reviewers and classifications are excluded from
  exports by construction, which is what makes a bundle unable to overwrite
  ratings. Add a natural key to any new model that has to travel.
- **Serving**: `pixi run serve` runs granian (async, uvloop, ASGI without
  lifespan) and mounts the collected static files itself; montages under
  `/media/` always go through the login-required Django view.
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
