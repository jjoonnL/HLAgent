#!/usr/bin/env bash
# Build on hosts that cannot set seccomp options on docker build.
set -euo pipefail

script_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
repo_root=$(CDPATH= cd -- "$script_dir/../.." && pwd)
image_name=hlagent-hatk:layout-v1
base_image=python:3.11-slim-bookworm
context=$(mktemp -d)
build_container=""

cleanup() {
    if [ -n "$build_container" ]; then
        docker rm -f "$build_container" >/dev/null || \
            printf 'Could not remove build container: %s\n' "$build_container" >&2
    fi
    rm -rf -- "$context"
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

docker info >/dev/null
if ! docker image inspect "$base_image" >/dev/null 2>&1; then
    docker pull "$base_image"
fi
base_id=$(docker image inspect --format '{{.Id}}' "$base_image")

# Copy only build inputs; repository settings, datasets, and results stay outside.
mkdir -p "$context/hlagent"
cp "$script_dir/install.sh" "$script_dir/requirements.txt" "$context/hlagent/"
tar -C "$repo_root" --exclude='__pycache__' --exclude='*.pyc' --exclude='.env' -cf - \
    hlagent/__init__.py hlagent/paths.py hlagent/analysis vendor/hatk \
    | tar -C "$context/hlagent" -xf -

build_container=$(docker create --security-opt seccomp=unconfined --user 0:0 \
    --env PYTHONDONTWRITEBYTECODE=1 --env PYTHONUNBUFFERED=1 --env PYTHONPATH=/opt/hlagent \
    --env OMP_NUM_THREADS=1 --env OPENBLAS_NUM_THREADS=1 \
    --entrypoint /bin/sh "$base_id" -c 'sh /opt/hlagent/install.sh')
# docker commit excludes mounted volumes, so copy build inputs into its filesystem.
docker cp "$context/hlagent" "$build_container:/opt/"
printf 'Installing HATK dependencies in build container: %s\n' "$build_container"
docker start --attach "$build_container"
exit_code=$(docker inspect --format '{{.State.ExitCode}}' "$build_container")
if [ "$exit_code" != "0" ]; then
    printf 'Installation failed with exit code %s. No image was committed.\n' "$exit_code" >&2
    exit 1
fi

image_id=$(docker commit \
    --change 'WORKDIR /work' --change 'USER 10001:10001' \
    --change 'ENTRYPOINT ["python", "-u", "-m", "hlagent.analysis.hatk_worker"]' \
    --change 'CMD ["--check"]' \
    --change "LABEL org.hlagent.build.base=$base_id" \
    --change 'LABEL org.hlagent.build.method=legacy-run-commit' \
    "$build_container" "$image_name")
docker run --rm --security-opt seccomp=unconfined "$image_id" --check >/dev/null
printf 'Image ready: %s\nImage ID: %s\n' "$image_name" "$image_id"
