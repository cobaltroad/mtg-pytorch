"""Evaluate the full decompose → tag pipeline for a commander.

For each mechanic key fired by decompose.py, queries the database using the
SQL from commander_mechanics.py and prints the cards that match — exactly
what the deck-building synergy engine would retrieve.

Consumer keys query ``cards`` directly (e.g. type_line / oracle_text filters).
Producer keys query via ``card_abilities.trigger_event`` (rows written by tag.py).

Usage
-----
    docker compose run --rm ingest python -m scripts.eval_decomposition "Tyvar the Bellicose"
    docker compose run --rm ingest python -m scripts.eval_decomposition tyvar --limit 20
    docker compose run --rm ingest python -m scripts.eval_decomposition tyvar --key mana_dork
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

import psycopg2
import psycopg2.extras

sys.path.insert(0, str(Path(__file__).parent.parent))

from stages.decompose import ORACLE_PATTERNS, _detect, _fetch
from mtg_sql import commanders
from synergy.commander_mechanics import (
    DECK_KEY_LABELS,
    PATTERN_KEY_TO_CONSUMER_SQL,
    PATTERN_KEY_TO_PRODUCER_SQL,
    PRODUCER_DECOMPOSE_TO_DECK_KEY,
)

DATABASE_URL = os.environ.get("DATABASE_URL", "").replace(
    "postgresql+asyncpg://", "postgresql://"
)


def _query_cards(where_sql: str, limit: int, conn) -> list[dict]:
    # Build the query as a plain string — where_sql comes from hardcoded
    # commander_mechanics.py fragments and limit is a CLI int, so no injection
    # risk.  Avoid psycopg2 param substitution entirely: % in LIKE clauses and
    # {g}-style mana symbols in the WHERE fragments would corrupt it.
    query = (
        "SELECT name, mana_cost, type_line, left(oracle_text, 120) AS oracle_snippet"
        " FROM cards"
        f" WHERE ({where_sql})"
        " ORDER BY name"
        f" LIMIT {limit}"
    )
    with conn.cursor(cursor_factory=psycopg2.extras.DictCursor) as cur:
        cur.execute(query)
        return [dict(r) for r in cur.fetchall()]


def _print_section(
    role: str,
    key: str,
    label: str,
    phrase: str,
    where_sql: str,
    cards: list[dict],
) -> None:
    count = len(cards)
    print(f"\n  [{role}] {key}  —  {label}")
    print(f'  matched: "{phrase[:70]}"')
    print(f"  SQL:     {where_sql[:100]}{'…' if len(where_sql) > 100 else ''}")
    print(f"  cards ({count}):")
    if not cards:
        print("    (none — tag.py may not have run yet)")
        return
    for c in cards:
        cost = c["mana_cost"] or ""
        tl = c["type_line"] or ""
        snippet = (c["oracle_snippet"] or "").replace("\n", " ")
        print(f"    {c['name']:<35} {cost:<12} {tl}")
        if snippet:
            print(f"      {snippet[:100]}")


def list_no_signals(limit: int) -> None:
    """Print all legal commanders for which _detect() fires no patterns."""
    if not DATABASE_URL:
        sys.exit("DATABASE_URL environment variable is required.")

    conn = psycopg2.connect(DATABASE_URL)
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.DictCursor) as cur:
            cur.execute(
                "SELECT name,"
                "       COALESCE(oracle_text, '')  AS oracle_text,"
                "       COALESCE(type_line,   '')  AS type_line,"
                "       COALESCE(color_identity, ARRAY[]::text[]) AS color_identity"
                " FROM cards"
                f" WHERE {commanders.WHERE}"
                " ORDER BY name"
            )
            commanders = cur.fetchall()
    finally:
        conn.close()

    gaps = [
        cmd for cmd in commanders if not _detect(cmd["oracle_text"], cmd["type_line"])
    ]

    total = len(commanders)
    print(f"Commanders with zero signals: {len(gaps)} / {total}")
    shown = gaps[:limit] if limit else gaps
    for cmd in shown:
        ci = "".join(cmd["color_identity"] or []) or "C"
        print(f"  {cmd['name']:<50}  [{ci}]")
    if limit and len(gaps) > limit:
        print(f"  … (capped at {limit}; pass --limit 0 for all)")


def eval_commander(name: str, limit: int, key_filter: str | None) -> None:
    if not DATABASE_URL:
        sys.exit("DATABASE_URL environment variable is required.")

    cards = _fetch(name)
    if not cards:
        sys.exit(f"No legal commander found matching: {name!r}")

    conn = psycopg2.connect(DATABASE_URL)
    try:
        for card in cards:
            oracle_text = card.get("oracle_text") or ""
            type_line = card.get("type_line") or ""
            ci = "".join(card.get("color_identity") or []) or "C"

            hits = _detect(oracle_text, type_line)

            print(f"\n{'═' * 64}")
            print(f"  {card['name']}  [{ci}]  {type_line}")
            print(f"{'═' * 64}")
            if oracle_text:
                for line in oracle_text.strip().splitlines():
                    print(f"  {line}")
            print()

            for key, label, phrase in hits:
                if key_filter and key != key_filter:
                    continue

                in_consumer = key in PATTERN_KEY_TO_CONSUMER_SQL
                in_producer = key in PRODUCER_DECOMPOSE_TO_DECK_KEY

                if not in_consumer and not in_producer:
                    print(f"  [TODO]  {key}  —  {label}")
                    print(f'          matched: "{phrase[:70]}"')
                    print(f"          (no SQL entry in commander_mechanics.py yet)")
                    continue

                if in_consumer:
                    where = PATTERN_KEY_TO_CONSUMER_SQL[key]
                    result = _query_cards(where, limit, conn)
                    _print_section("CONSUMER", key, label, phrase, where, result)

                if in_producer:
                    for deck_key in PRODUCER_DECOMPOSE_TO_DECK_KEY[key]:
                        if deck_key not in PATTERN_KEY_TO_PRODUCER_SQL:
                            continue
                        deck_label = DECK_KEY_LABELS.get(deck_key, deck_key)
                        where = PATTERN_KEY_TO_PRODUCER_SQL[deck_key]
                        result = _query_cards(where, limit, conn)
                        _print_section(
                            f"PRODUCER → {deck_key} ({deck_label})",
                            key,
                            label,
                            phrase,
                            where,
                            result,
                        )

                print()
    finally:
        conn.close()


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Show cards matched by decompose+tag for a commander."
    )
    parser.add_argument(
        "name",
        nargs="?",
        default=None,
        help="Commander name (partial, case-insensitive). Required unless --no-signals.",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=10,
        help="Max cards to show per key, or commanders to show with --no-signals (default: 10; 0 = all)",
    )
    parser.add_argument(
        "--key",
        default=None,
        help="Only evaluate a specific pattern key (e.g. mana_dork)",
    )
    parser.add_argument(
        "--no-signals",
        action="store_true",
        help="List all legal commanders that fire zero decompose patterns (gap analysis).",
    )
    args = parser.parse_args()

    if args.no_signals:
        list_no_signals(args.limit)
    elif args.name:
        eval_commander(args.name, args.limit, args.key)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
