#!/usr/bin/env bash
# Usage: ./lookup.sh <card name>
# Looks up a Magic: The Gathering card in the MTGJSON AtomicCards cache.
set -euo pipefail
docker compose run --rm --no-deps ingest python3 /app/lookup_card.py "$@"
