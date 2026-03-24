"""
Spot-check the commander decomposition JSON.

Loads ``commander_decomposition.json`` and pretty-prints the full signal
breakdown for one or more commanders.  Accepts partial, case-insensitive
name matches.  Useful for verifying pattern coverage after running
``decompose_commanders.py``.

Usage
-----
Via Docker::

    # Single commander (partial match OK)
    docker compose run --rm ingest python scripts/eval_commander.py "Anje"

    # Exact name
    docker compose run --rm ingest python scripts/eval_commander.py "Syr Konrad, the Grim"

    # Show all commanders with zero signals (gap analysis)
    docker compose run --rm ingest python scripts/eval_commander.py --no-signals

    # Coverage summary only
    docker compose run --rm ingest python scripts/eval_commander.py --stats

    # Show commanders matching a pattern key
    docker compose run --rm ingest python scripts/eval_commander.py --pattern madness_payoff

    # Use a different input file
    docker compose run --rm ingest python scripts/eval_commander.py "Atraxa" \\
        --input /data/commander_decomposition.json

Options
-------
NAME
    Commander name (partial, case-insensitive).  May be repeated.

--input FILE
    Path to the JSON file (default: /data/commander_decomposition.json).

--no-signals
    List all commanders that produced zero signals.

--stats
    Print coverage summary and exit.

--pattern KEY
    List all commanders that matched a specific pattern_key.

--limit N
    When listing multiple results (--no-signals, --pattern), cap at N entries.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


# ── Formatting helpers ────────────────────────────────────────────────────────

_SOURCE_BADGE = {
    "oracle_text": "oracle",
    "xmage":       "xmage ",
}

_SCORE_BAR = {
    1.0: "████",
    0.9: "███▌",
    0.85: "███ ",
    0.8: "██▌ ",
    0.7: "██  ",
    0.6: "█▌  ",
}


def _score_bar(score: float) -> str:
    for threshold, bar in _SCORE_BAR.items():
        if score >= threshold:
            return bar
    return "█   "


def _print_commander(entry: dict) -> None:
    """Print a full signal breakdown for one commander entry."""
    name      = entry["name"]
    ci        = "".join(f"{{{c}}}" for c in (entry.get("color_identity") or []))
    cmc       = entry.get("cmc", 0)
    type_line = entry.get("type_line", "")
    xmage     = entry.get("xmage_file")
    signals   = entry.get("signals", [])
    unmatched = entry.get("unmatched_triggers", [])

    width = 66
    print("═" * width)
    print(f"  {name}")
    print(f"  {type_line}  |  CMC {cmc:.0f}  |  {ci}")
    print("═" * width)

    xmage_tag = f"✓  {xmage}" if xmage else "✗  no XMage file"
    print(f"  XMage: {xmage_tag}")
    print()

    if not signals:
        print("  (no signals detected)")
    else:
        # Group by source for clarity
        oracle_sigs = [s for s in signals if s.get("source") == "oracle_text"]
        xmage_sigs  = [s for s in signals if s.get("source") == "xmage"]

        if oracle_sigs:
            print(f"  Oracle text signals ({len(oracle_sigs)}):")
            for sig in oracle_sigs:
                bar   = _score_bar(sig["score"])
                print(f"    {bar} [{sig['score']:.2f}]  {sig['pattern_key']}")
                phrase = sig.get("matched_phrase", "")
                if phrase:
                    print(f"           matched: \"{phrase}\"")
            print()

        if xmage_sigs:
            print(f"  XMage signals ({len(xmage_sigs)}):")
            for sig in xmage_sigs:
                bar = _score_bar(sig["score"])
                ac  = sig.get("ability_class", "?")
                ec  = sig.get("effect_class") or "—"
                print(f"    {bar} [{sig['score']:.2f}]  {sig['pattern_key']}")
                print(f"           class:  {ac}")
                print(f"           effect: {ec}")
            print()

    if unmatched:
        print(f"  Unmatched trigger clauses ({len(unmatched)}):")
        for clause in unmatched:
            print(f"    ○  {clause}")
    else:
        print("  Unmatched triggers: none")

    print("─" * width)


# ── Search helpers ────────────────────────────────────────────────────────────

def _find_by_name(entries: list[dict], query: str) -> list[dict]:
    """Return all entries whose name contains query (case-insensitive)."""
    q = query.lower()
    # Exact match first
    exact = [e for e in entries if e["name"].lower() == q]
    if exact:
        return exact
    # Prefix match
    prefix = [e for e in entries if e["name"].lower().startswith(q)]
    if prefix:
        return prefix
    # Substring match
    return [e for e in entries if q in e["name"].lower()]


def _find_by_pattern(entries: list[dict], pattern_key: str) -> list[dict]:
    """Return all entries that matched a given pattern_key."""
    return [
        e for e in entries
        if any(s["pattern_key"] == pattern_key for s in e.get("signals", []))
    ]


# ── Coverage summary ──────────────────────────────────────────────────────────

def _print_stats(entries: list[dict]) -> None:
    from collections import Counter

    total = len(entries)
    xmage_found = sum(1 for e in entries if e.get("xmage_file"))
    sig_counts  = Counter(len(e.get("signals", [])) for e in entries)

    oracle_only = sum(
        1 for e in entries
        if any(s["source"] == "oracle_text" for s in e.get("signals", []))
        and not any(s["source"] == "xmage" for s in e.get("signals", []))
    )
    xmage_only = sum(
        1 for e in entries
        if any(s["source"] == "xmage" for s in e.get("signals", []))
        and not any(s["source"] == "oracle_text" for s in e.get("signals", []))
    )
    both = sum(
        1 for e in entries
        if any(s["source"] == "oracle_text" for s in e.get("signals", []))
        and any(s["source"] == "xmage" for s in e.get("signals", []))
    )
    no_signals = sig_counts.get(0, 0)

    # Pattern key frequencies
    pattern_freq: Counter = Counter()
    for e in entries:
        for s in e.get("signals", []):
            pattern_freq[s["pattern_key"]] += 1

    print("═" * 66)
    print("  Commander Decomposition — Coverage Summary")
    print("═" * 66)
    print(f"  Total commanders:     {total}")
    print(f"  XMage file found:     {xmage_found} ({100*xmage_found/max(total,1):.1f}%)")
    print(f"  No XMage file:        {total-xmage_found} ({100*(total-xmage_found)/max(total,1):.1f}%)")
    print()
    print("  Signal coverage:")
    for n in [0, 1, 2]:
        count = sig_counts.get(n, 0)
        label = f"  {n} signal{'s' if n != 1 else ''}:"
        print(f"    {label:<18} {count:5d}  ({100*count/max(total,1):.1f}%)")
    three_plus = sum(v for k, v in sig_counts.items() if k >= 3)
    print(f"    {'  3+ signals:':<18} {three_plus:5d}  ({100*three_plus/max(total,1):.1f}%)")
    print()
    print("  Source breakdown:")
    print(f"    oracle text only:   {oracle_only:5d}  ({100*oracle_only/max(total,1):.1f}%)")
    print(f"    xmage only:         {xmage_only:5d}  ({100*xmage_only/max(total,1):.1f}%)")
    print(f"    both sources:       {both:5d}  ({100*both/max(total,1):.1f}%)")
    print(f"    no signals:         {no_signals:5d}  ({100*no_signals/max(total,1):.1f}%)")
    print()
    print("  Top pattern keys (by commander count):")
    for key, count in pattern_freq.most_common(20):
        bar = "█" * min(int(count / max(total, 1) * 40), 40)
        print(f"    {key:<35} {count:4d}  {bar}")
    print("─" * 66)


# ── CLI ───────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Spot-check the commander decomposition JSON.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "names",
        nargs="*",
        metavar="NAME",
        help="Commander name(s) to look up (partial, case-insensitive).",
    )
    parser.add_argument(
        "--input",
        type=Path,
        default=Path("/data/commander_decomposition.json"),
        help="Path to the decomposition JSON (default: /data/commander_decomposition.json).",
    )
    parser.add_argument(
        "--no-signals",
        action="store_true",
        help="List all commanders that produced zero signals.",
    )
    parser.add_argument(
        "--stats",
        action="store_true",
        help="Print coverage summary and exit.",
    )
    parser.add_argument(
        "--pattern",
        metavar="KEY",
        help="List all commanders that matched a specific pattern_key.",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=50,
        metavar="N",
        help="Cap results for --no-signals / --pattern (default: 50).",
    )
    args = parser.parse_args()

    if not args.input.exists():
        sys.exit(
            f"File not found: {args.input}\n"
            "Run: docker compose run --rm ingest python scripts/decompose_commanders.py"
        )

    with args.input.open(encoding="utf-8") as fh:
        entries: list[dict] = json.load(fh)

    print(f"Loaded {len(entries)} commanders from {args.input}\n")

    # --stats
    if args.stats:
        _print_stats(entries)
        return

    # --no-signals
    if args.no_signals:
        gaps = [e for e in entries if not e.get("signals")]
        print(f"Commanders with zero signals: {len(gaps)}")
        if args.limit:
            gaps = gaps[: args.limit]
        for e in gaps:
            ci = "".join(e.get("color_identity") or [])
            print(f"  {e['name']:<50}  [{ci}]  cmc={e.get('cmc',0):.0f}")
        if len(gaps) == args.limit:
            print(f"  … (capped at {args.limit}; pass --limit 0 for all)")
        return

    # --pattern
    if args.pattern:
        matches = _find_by_pattern(entries, args.pattern)
        print(f"Commanders matching pattern '{args.pattern}': {len(matches)}")
        if args.limit:
            matches = matches[: args.limit]
        for e in matches:
            ci  = "".join(e.get("color_identity") or [])
            sig = next(s for s in e["signals"] if s["pattern_key"] == args.pattern)
            src = sig["source"]
            print(f"  [{src:<11}]  {e['name']:<50}  [{ci}]")
        if len(matches) == args.limit:
            print(f"  … (capped at {args.limit}; pass --limit 0 for all)")
        return

    # Named lookup
    if not args.names:
        parser.print_help()
        print("\nTip: pass --stats for a coverage summary, or --no-signals for gap analysis.")
        return

    for query in args.names:
        results = _find_by_name(entries, query)
        if not results:
            print(f"No commander found matching: '{query}'")
            continue
        if len(results) > 1 and len(query) < 5:
            # Ambiguous short query — list matches instead of dumping all
            print(f"Multiple commanders match '{query}' ({len(results)} results):")
            for e in results[:20]:
                print(f"  {e['name']}")
            if len(results) > 20:
                print(f"  … and {len(results)-20} more. Refine your query.")
            continue
        for entry in results:
            _print_commander(entry)


if __name__ == "__main__":
    main()
