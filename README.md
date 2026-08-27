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
pixi run manage createsuperuser
```

Enable the git hooks (ruff, ty, codespell, Conventional Commits) once:

```sh
prek install
```

## Ingest a run

Point `import_run` at a MELODIC+pyFIX derivatives directory (containing
`filtered_func_data.ica/`, `mc/prefiltered_func_data_mcf.par`,
`fix/features.csv`, and `fix4melview_<MODEL>_thr<N>.txt`):

```sh
pixi run manage import_run /path/to/sub-XX_..._desc-preproc_bold
```

Montage rendering parallelizes across `--workers` processes (default:
CPUs − 2; a 96-component run takes a few seconds).

## Rate

```sh
pixi run serve
```

Then open <http://127.0.0.1:8000/>. Keyboard: `1`/`s` signal, `2`/`u` unknown,
`3`/`n` noise, `←`/`→` prev/next component, `g` jump to an IC number.
Human ratings are stored per user; the P(signal) strip in the verdict card
colors each component by its human rating once one exists, so disagreements
with FIX stand out as a color on the wrong side of the threshold line.

## Development

```sh
pixi run -e dev test        # pytest
pixi run -e dev ruff check .
pixi run -e dev ty check
pixi run mockups            # regenerate the static design mockups
```

Conventions: [HackSoft Django StyleGuide](https://github.com/HackSoftware/Django-Styleguide)
(services/selectors), typed Python checked by ty, ruff lint+format,
pytest with arrange-act-assert, SQLite in production (WAL,
`transaction_mode=IMMEDIATE`), and a thin htmx frontend (state lives on the
backend; the only custom JS is keyboard shortcuts and the montage-axis
toggle). The database (`db/`) and ingested montages (`media/`) contain
subject-derived data and stay untracked.
