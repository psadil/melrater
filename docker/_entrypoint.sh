#!/bin/bash

set -euo pipefail

# Both durable paths are hardcoded off BASE_DIR in settings.py (db/db.sqlite3 and
# media/), so they are mount points, not env-configurable. A typo'd -v does not
# fail loudly: settings.py's DB_DIR.mkdir(exist_ok=True) runs at import and
# happily creates /app/db in the container's writable layer, so every rating
# would be lost the next time the container is recreated. Compare device numbers
# rather than calling mountpoint(1) — both bind mounts and named volumes land on
# a different st_dev than the overlay root, and stat is coreutils.
if [ "${MELRATER_ALLOW_EPHEMERAL_STATE:-0}" != "1" ]; then
	root_dev="$(stat -c %d /app)"
	for d in /app/db /app/media; do
		if [ "$(stat -c %d "$d")" = "$root_dev" ]; then
			echo "FATAL: $d is not a mounted volume; refusing to start." >&2
			echo "       Data written there dies with the container. Mount it, e.g." >&2
			echo "         -v /srv/melrater/db:/app/db -v /srv/melrater/media:/app/media" >&2
			echo "       Set MELRATER_ALLOW_EPHEMERAL_STATE=1 for a throwaway smoke test." >&2
			exit 1
		fi
	done
fi

# WAL needs write permission on the DIRECTORY, not just on the database file, to
# create the -wal/-shm sidecars. A host directory left owned by root reads fine,
# so the site looks healthy right up until the first rating fails with an
# OperationalError; check it now instead.
for d in /app/db /app/media; do
	if ! touch "$d/.melrater-writable" 2>/dev/null; then
		echo "FATAL: $d is not writable by uid $(id -u)." >&2
		echo "       On the host: chown -R 57439:57439 the mounted directory." >&2
		exit 1
	fi
	rm -f "$d/.melrater-writable"
done

# The only start-up step a fresh database needs. Reviewer rows are created
# lazily during ingest, and there is no cache table to build (settings.py
# configures no CACHES). Idempotent, so it is safe on every start.
python -m django migrate --no-input

# exec the CMD rather than hardcoding granian: this one image also has to run
# `python -m django createsuperuser` / `import_run`, which dirt splits into a
# second image with its own entrypoint.
exec "$@"
