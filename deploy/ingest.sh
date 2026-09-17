#!/usr/bin/env bash
# Ingest a MELODIC+pyFIX derivatives tree and ship it to the deployment. Runs
# from the LAPTOP, not the server: ingest reads the raw NIfTIs and renders the
# montages here, and the server only ever receives finished bytes (see "Push
# runs to the deployment" in the README).
#
#   MELRATER_PUSH_USER=<ingest account> ./deploy/ingest.sh ~/git/derivatives
#
# Meant to be re-run against a tree the pipeline is still writing into, which is
# the whole point of it: re-indexing under the same dataset id replaces that
# root's rows rather than duplicating them, runs already in the local database
# are skipped, and runs the server already holds with the same montages are
# skipped there too. A half-written run is reported, skipped, and picked up by
# the next invocation once the pipeline finishes it.
#
# Override the target with MELRATER_SERVER, the catalog with MELRATER_CATALOG,
# or the catalog's dataset id with MELRATER_DATASET. The password is deliberately
# not handled here: import_run reads MELRATER_PUSH_PASSWORD or prompts for it,
# so the credential never reaches a command line.
set -euo pipefail

cd "$(dirname "$0")/.."

SRC=${1:?usage: ingest.sh <derivatives dir>}
# Absolute, because the catalog records the root it walked. A relative root
# resolves only for a later import_run launched from this same directory, and
# otherwise needs --base-dir to rebase it.
SRC=$(cd "$SRC" && pwd)

SERVER=${MELRATER_SERVER:-hetzner}
DATASET=${MELRATER_DATASET:-melodic}
CATALOG=${MELRATER_CATALOG:-db/$DATASET.duckdb}

# Checked before the index rather than at the push: the push is the last step,
# and discovering the account is missing there wastes the whole render.
: "${MELRATER_PUSH_USER:?set it to an account in the deployment ingest group}"

# The CLI writes the catalog the wheel queries, so the two pins have to agree. A
# CLI older than the pin writes no feat_icstats table, and import_run then
# reports *every* run as "feat_icstats has no rows for the icstats file" --
# which reads as bad data rather than as a stale indexer.
pinned=$(awk '/^\[pypi-dependencies\]$/{s=1;next} /^\[/{s=0} s' pixi.toml |
	sed -n 's/^bidslake = .*rev = "\([0-9a-f]\{40\}\)".*/\1/p')
if [ -z "$pinned" ]; then
	echo "error: could not read the bidslake pin out of pixi.toml" >&2
	exit 1
fi
installed=$(bidslake --version 2>/dev/null || true)
case "$installed" in
*"${pinned:0:7}"*) ;;
*)
	echo "error: the bidslake CLI is not the rev pixi.toml pins" >&2
	echo "  pinned:    ${pinned:0:7}" >&2
	echo "  installed: ${installed:-none on PATH}" >&2
	echo "  cargo install --locked --force --git https://github.com/psadil/bidslake.git --rev $pinned bidslake" >&2
	exit 1
	;;
esac

echo "==> 1/2 index $SRC"
bidslake index -i "$SRC" --adapter feat --dataset-id "$DATASET" -o "$CATALOG"

# The box's own answer for its public address, never a hardcoded one. /melrater
# is the path prefix it is served under, and push.py joins this with
# /api/v1/runs -- so leaving the prefix off posts to the edge's 404, not the app.
URL="https://$(ssh "$SERVER" vm-host)/melrater"

echo "==> 2/2 ingest and push to $URL"
# Not left to `set -e`: import_run exits non-zero whenever any run failed, and a
# tree still being written into reliably has one. A bare non-zero exit there
# reads as a broken script rather than as "the newest run is not finished yet".
status=0
pixi run -e render manage import_run "$CATALOG" \
	--push "$URL" --user "$MELRATER_PUSH_USER" || status=$?
if [ "$status" -ne 0 ]; then
	echo
	echo "some runs were not ingested (named above). A tree the pipeline is" >&2
	echo "still writing into always has one; re-run once those runs finish." >&2
fi
exit "$status"
