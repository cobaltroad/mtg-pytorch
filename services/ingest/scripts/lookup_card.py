#!/usr/bin/env python3
"""Look up a Magic: The Gathering card in the MTGJSON AtomicCards cache."""
import json
import subprocess
import sys


def main():
    if len(sys.argv) < 2:
        print("Usage: lookup_card.py <card name>")
        sys.exit(1)

    query_raw = " ".join(sys.argv[1:])

    result = subprocess.run(
        ["find", "/data", "-name", "mtgjson_AtomicCards.json"],
        capture_output=True, text=True,
    )
    candidates = [p for p in result.stdout.strip().split("\n") if p]
    if not candidates:
        print("AtomicCards cache not found — run: docker compose run --rm ingest")
        sys.exit(1)
    path = candidates[0]

    data = json.load(open(path))["data"]

    query = query_raw.lower()
    matches = [k for k in data if k.lower() == query]
    if not matches:
        matches = [k for k in data if query in k.lower()]
    if not matches:
        print(f"Card not found: {query_raw}")
        sys.exit(1)

    if matches[0].lower() != query:
        print(f"(closest match: {matches[0]})")

    card_name = matches[0]
    face = data[card_name][0]
    ci = face.get("colorIdentity", [])
    print(f"=== {card_name} ===")
    print(f"Mana Cost : {face.get('manaCost') or '—'}  (CMC {face.get('manaValue', '?')})")
    print(f"Type      : {face.get('type', '—')}")
    print(f"Colors    : {face.get('colors', [])}   Identity: {ci}")
    if face.get("power"):
        print(f"P/T       : {face['power']}/{face['toughness']}")
    if face.get("loyalty"):
        print(f"Loyalty   : {face['loyalty']}")
    kw = face.get("keywords", [])
    if kw:
        print(f"Keywords  : {', '.join(kw)}")
    legal = face.get("legalities", {}).get("commander", "unknown")
    print(f"Commander : {legal}")
    print()
    print(face.get("text") or "(no oracle text)")


if __name__ == "__main__":
    main()
