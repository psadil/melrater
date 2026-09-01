#!/usr/bin/env bash
# Deploy melrater. Runs from the LAPTOP, not the server — the image cannot be
# built on the box (the build compiles bidslake, a Rust extension bundling
# DuckDB's C++; see "Build and ship" in the README), so the whole sequence
# starts here and the server only ever receives a finished image and two config
# files.
#
#   ./deploy/deploy.sh
#
# Override the target with MELRATER_SERVER (the default is the ~/.ssh/config
# Host block the README sets up under "SSH access") or the tag with MELRATER_TAG.
set -euo pipefail

cd "$(dirname "$0")/.."

SERVER=${MELRATER_SERVER:-hetzner}
TAG=${MELRATER_TAG:-psadil/melrater:latest}

# The commit stamped onto the server in step 5 would be a lie about a dirty
# tree, and "which config is actually deployed" is the question this exists to
# answer. Refuse rather than record something untrue.
if [ -n "$(git status --porcelain)" ]; then
	echo "working tree is dirty — commit before deploying" >&2
	exit 1
fi
SHA=$(git rev-parse HEAD)

echo "==> 1/6 build for linux/amd64"
docker buildx build --platform=linux/amd64 \
	--provenance=mode=max --sbom=true \
	-t "$TAG" --load .

# Emulation yields a subtly wrong binary far more readily than it yields a
# failed build, so a green build proves nothing by itself.
echo "==> 2/6 smoke-test the emulated binary"
docker run --rm --entrypoint python "$TAG" \
	-c "import bidslake, polars, numpy, nibabel, PIL, pillow_avif; print('extensions ok')"

# Registry path: carries the provenance attestation and the SBOM. For the
# registry-free route swap this for
#   docker save "$TAG" | ssh "$SERVER" 'docker load'
# and drop the `docker compose pull` in step 5 — it fails with "denied" when
# nothing was ever pushed, and `set -e` would abort the deploy there.
echo "==> 3/6 push the image"
docker push "$TAG"

# Named files only. Never a directory sync and never --delete: db/, media/ and
# backups/ live in that same directory. (TLS/routing config belongs to the
# proxy repo and never travels from here.)
echo "==> 4/6 ship the config"
rsync -av deploy/compose.yaml "$SERVER":/srv/melrater/

echo "==> 5/6 restart and record what is running"
ssh "$SERVER" "set -euo pipefail
	cd /srv/melrater
	export MELRATER_TAG='$TAG'   # or compose deploys its default, not what we built
	docker compose pull
	docker compose up -d
	docker image prune -f
	printf 'config %s\nimage  %s\ndate   %s\n' \
		'$SHA' \"\$(docker image inspect -f '{{.Id}}' '$TAG')\" \"\$(date -Is)\" \
		> /srv/melrater/DEPLOYED"

# `up -d` reports success whether or not it actually replaced anything, so
# compare image IDs rather than trusting it.
echo "==> 6/6 verify"
echo "  laptop image: $(docker image inspect -f '{{.Id}}' "$TAG")"
ssh "$SERVER" 'cat /srv/melrater/DEPLOYED; docker ps --format "  {{.Names}}\t{{.Status}}"'
