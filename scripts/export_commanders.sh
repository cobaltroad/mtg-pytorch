#!/usr/bin/env bash
# Export mtg_commanders.pt (Phases 3-4 training artifact).
# Resolves GIT_COMMIT from the host repo and passes it into the container.
set -euo pipefail

GIT_COMMIT=$(git rev-parse HEAD)
export GIT_COMMIT

echo "Exporting mtg_commanders.pt  (git_commit=${GIT_COMMIT})"
docker compose run --rm \
    -e GIT_COMMIT="$GIT_COMMIT" \
    ingest python pipeline.py --stage export_dataset_commanders "$@"
