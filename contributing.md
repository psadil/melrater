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
```

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
  Django-free and fully typed.
- **Typing**: checked by ty. Django's ORM is invisible to static checkers
  (no ty plugin), so `unresolved-attribute` is ignored in the ORM-glue files
  only (see `pyproject.toml`). Everywhere else stays fully checked: JSONField
  payloads have pydantic schemas (`schemas.py`), and selectors project ORM
  rows into those typed models at the boundary — add fields to the schema
  rather than reaching into raw JSON or `cast`ing.
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
- **SQLite in production**: WAL journal, `IMMEDIATE` transactions
  (see `config/settings.py`); don't add a second database backend.
