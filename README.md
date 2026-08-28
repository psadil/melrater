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

Montage rendering parallelizes across `--workers` processes (default:
CPUs − 2; a 96-component run takes a few seconds).

## Rate

```sh
pixi run serve
```

This collects static files and starts [granian](https://github.com/emmett-framework/granian)
(async, uvloop) on <http://127.0.0.1:8000/>. Keyboard: `1`/`s` signal, `2`/`u` unknown,
`3`/`n` noise, `←`/`→` prev/next component, `g` jump to an IC number.
Human ratings are stored per user; the P(signal) strip in the verdict card
colors each component by its human rating once one exists, so disagreements
with FIX stand out as a color on the wrong side of the threshold line.

## Deploy

The app also runs from a container: one image that both serves and runs
management commands, with the SQLite database and the rendered montages as
host bind mounts.

```sh
docker buildx build --platform=linux/amd64 --provenance=mode=max --sbom=true -t melrater .
```

See [deploy.md](deploy.md) for the rest — where the database goes, how to move
it without losing WAL contents, and the Hetzner-side commands.

## Development

```sh
pixi run -e dev test        # unit tests
pixi run -e dev test-e2e    # playwright browser tests
pixi run -e dev ruff check .
pixi run -e dev ty check
```

See [contributing.md](contributing.md) for layout and conventions. The
database (`db/`) and ingested montages (`media/`) contain subject-derived
data and stay untracked.
