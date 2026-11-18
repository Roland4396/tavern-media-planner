#!/bin/sh
# Input can be a previously archived wheelhouse. No live service, credential or GPU is required.
set -eu
src=$(CDPATH= cd -- "$(dirname "$0")" && pwd)
base=python:3.12-slim-bookworm@sha256:935c3c5b12357a7e10caa9c1e6ca5692344d97cf229832ef584be2cec9fc03df
name=$(cd "$src" && sha256sum Dockerfile requirements.lock service.py sequence_planner.py | sha256sum | cut -c1-12)
ctx=$(mktemp -d); trap 'rm -rf "$ctx"' EXIT
mkdir "$ctx/wheelhouse"; cp "$src/Dockerfile" "$src/requirements.lock" "$src/service.py" "$src/sequence_planner.py" "$ctx/"
if [ "$#" -eq 1 ]; then cp "$1"/*.whl "$ctx/wheelhouse/"
elif [ "$#" -eq 0 ]; then
    docker run --rm --user "$(id -u):$(id -g)" -v "$ctx:/build" --entrypoint python "$base" -m pip download --disable-pip-version-check --no-cache-dir --only-binary=:all: --no-deps --require-hashes -d /build/wheelhouse -r /build/requirements.lock
else echo 'usage: build.sh [offline-wheelhouse]' >&2; exit 2; fi
docker build --network none --pull=false -t "local/tavern-media:sequence-planner-$name" "$ctx"
