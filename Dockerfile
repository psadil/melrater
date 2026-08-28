# One image: it serves (the CMD below) and it runs management commands
# (`docker compose run --rm melrater python -m django import_run …`). Unlike
# dirt there is no web/manage split to make — the serving path itself imports
# bidslake (config/urls.py -> core/urls.py -> views.py:11 -> services.py:12 ->
# lake.py), so a bidslake-free web environment does not exist here.
#
#   docker buildx build --platform=linux/amd64 \
#     --provenance=mode=max --sbom=true -t ghcr.io/psadil/melrater .
#
# mode=max (rather than the default min) embeds the Dockerfile source, all ten
# build steps and one further digest-pinned base image; both modes record the
# git revision and remote. It also records --build-arg VALUES, so never pass a
# secret that way — the ARGs below are all non-secret. Attestations make the
# output a manifest list, which `--load` can only store when Docker's containerd
# image store is enabled; without it, push to a registry instead. Note that
# `docker save` drops attestations, so the registry is the only transport that
# carries provenance to the far end.
#
# The platform is not optional: pixi.lock resolves osx-arm64 and linux-64 only,
# so `pixi install --locked` cannot satisfy an arm64 Linux build (a Hetzner CAX
# instance is Ampere arm64 — that needs a linux-aarch64 platform and a re-lock).

# The pixi-docker image tag is <version>-<distro>; -noble pins glibc parity with
# the ubuntu:24.04 runtime. 0.77.1 is the pixi that wrote pixi.lock, so --locked
# cannot trip over a lockfile-format skew.
ARG PIXI_VERSION=0.77.1
ARG BASE_IMAGE=ubuntu:24.04
# Set to 0 to keep the rust/gcc toolchain in the runtime environment — the first
# thing to try if a build fails only after the prune step below.
ARG PRUNE_TOOLCHAIN=1

# --------------------------------------------------------------------------
# Builder — the official pixi image (pixi preinstalled, multi-arch).
#   git: pixi builds git-sourced pypi deps, and bidslake is one.
# Deliberately no build-essential: the environment ships conda's gcc/gxx on a
# 2.28 sysroot, and a system compiler on PATH lets cc-rs build duckdb's bundled
# C++ against Ubuntu's 2.39 glibc while conda's linker resolves against the
# older one — undefined __isoc23_* symbols. Same reasoning as the comment on
# pixi.toml's [target.linux-64.dependencies].
# --------------------------------------------------------------------------
FROM ghcr.io/prefix-dev/pixi:${PIXI_VERSION}-noble AS builder
ARG PRUNE_TOOLCHAIN
RUN apt-get update \
    && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends git \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /app

# The whole copy set. melrater is not installed into the environment (pyproject
# has no [project] table and pixi.toml no editable self-install), so pixi reads
# the manifest and the lock and nothing else — dirt needs COPY --parents only
# because its editable install requires its packages to physically exist.
# Keeping src/ out of this stage means editing a template does not invalidate
# the layer that compiles bidslake from Rust.
COPY pixi.toml pixi.lock /app/
RUN pixi install --locked -e default

# Most of this environment is build-only: rust, rust-std, gcc/gxx, the 2.28
# sysroot and maturin exist solely so `pixi install` could compile bidslake
# above. Deleting them is safe because pixi never runs at runtime — the runtime
# stage activates the environment with PATH + CONDA_PREFIX alone. Left in
# place: lib/libstdc++.so.6 and lib/libgcc_s.so.1, which are separate runtime
# packages that the extension modules actually link against. The runtime
# stage's `django check` is the gate that proves this cut nothing load-bearing.
RUN if [ "${PRUNE_TOOLCHAIN}" = "1" ]; then cd /app/.pixi/envs/default && rm -rf \
      lib/rustlib lib/libLLVM* lib/librustc_driver* lib/librustc-stable_rt* \
      bin/cargo bin/cargo-clippy bin/cargo-fmt bin/clippy-driver bin/rustc \
      bin/rustdoc bin/rustfmt bin/rust-gdb bin/rust-gdbgui bin/rust-lldb bin/maturin \
      x86_64-conda-linux-gnu lib/gcc libexec/gcc include/c++ \
      bin/x86_64-conda-linux-gnu-* \
      share/doc share/man ; fi

# --------------------------------------------------------------------------
# Runtime.
# --------------------------------------------------------------------------
FROM ${BASE_IMAGE} AS runtime

ARG MAMBA_USER=mambauser
ARG MAMBA_USER_ID=57439
ARG MAMBA_USER_GID=57439
ENV MAMBA_USER=$MAMBA_USER
ENV MAMBA_USER_ID=$MAMBA_USER_ID
ENV MAMBA_USER_GID=$MAMBA_USER_GID

COPY --chmod=0544 docker/_dockerfile_initialize_user_accounts.sh /usr/local/bin/_dockerfile_initialize_user_accounts.sh
RUN /usr/local/bin/_dockerfile_initialize_user_accounts.sh

# Create /app and the two mount points as the app user before switching to it:
# a bare WORKDIR would create them as root, and settings.py runs
# DB_DIR.mkdir(exist_ok=True) at import time, so /app must be writable by 57439.
RUN install -d -o $MAMBA_USER_ID -g $MAMBA_USER_GID /app /app/db /app/media

USER $MAMBA_USER
WORKDIR /app

COPY --from=builder --chown=$MAMBA_USER:$MAMBA_USER /app/.pixi /app/.pixi

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV LANG=C.UTF-8 LC_ALL=C.UTF-8

# Four variables where dirt needs two: dirt gets PYTHONPATH from its editable
# install's .pth file and DJANGO_SETTINGS_MODULE from its `manage` console
# script, while melrater gets both from pixi.toml's [activation.env] — which
# only applies under `pixi run`, and pixi is not on the runtime image. Without
# PYTHONPATH: ModuleNotFoundError: config. Without DJANGO_SETTINGS_MODULE:
# ImproperlyConfigured. CONDA_PREFIX must match the builder path exactly (the
# environment's libraries resolve data files relative to their build prefix);
# the activate.d scripts present export build-time variables only, so PATH and
# CONDA_PREFIX are a complete activation.
ENV PATH=/app/.pixi/envs/default/bin:$PATH
ENV CONDA_PREFIX=/app/.pixi/envs/default
ENV PYTHONPATH=/app/src
ENV DJANGO_SETTINGS_MODULE=config.settings

# Last churning layer: only a source edit invalidates anything below here.
COPY --chown=$MAMBA_USER:$MAMBA_USER src /app/src

# Bake the static files. STATIC_ROOT is /app/staticfiles, nothing mounts over
# it, and granian refuses to start when its static mount directory is missing —
# so collecting here (rather than in the entrypoint, as dirt does) makes it an
# immutable layer and takes the step off the start path. The throwaway key is
# explicit rather than leaning on MELRATER_DEBUG's "1" default, so a later
# settings change cannot turn this into ImproperlyConfigured. `check` shares the
# RUN because it resolves the URLconf, which is what imports bidslake: an
# over-eager prune fails the build here instead of 500ing every request.
# `check` opens the database connection, and SQLite creates the file on
# connect — so an empty db.sqlite3 would otherwise ship inside the image,
# sitting under the mount point where only confusion can come of it.
RUN MELRATER_DEBUG=0 MELRATER_SECRET_KEY=build-only-not-a-secret \
    python -m django collectstatic --noinput \
 && MELRATER_DEBUG=0 MELRATER_SECRET_KEY=build-only-not-a-secret \
    python -m django check \
 && rm -f /app/db/db.sqlite3

COPY --chmod=0555 docker/_entrypoint.sh /usr/local/bin/_entrypoint

EXPOSE 8000
ENTRYPOINT [ "/usr/local/bin/_entrypoint" ]
# The `serve` task's flags, with three changes: 0.0.0.0 (127.0.0.1 is
# unreachable from outside the container), an absolute static mount (the task's
# relative "staticfiles" only resolves because pixi sets cwd to the repo root),
# and a short expiry — asset names are unhashed, so granian's 86400 s default
# would keep a CSS fix from reaching a reviewer for a day.
CMD [ "granian", "config.asgi:application", \
      "--interface", "asginl", \
      "--host", "0.0.0.0", "--port", "8000", \
      "--workers", "2", "--runtime-mode", "st", "--loop", "uvloop", \
      "--static-path-route", "/static", "--static-path-mount", "/app/staticfiles", \
      "--static-path-expires", "300", \
      "--no-ws" ]
