"""
Phase 4 + Phase 5 deck evaluator.

Scores all legal candidates against the commander with the trained CardEncoder,
then assembles a legal 99-card deck via deck_builder.py (Phase 5).  When
DATABASE_URL is set, deck_builder queries the DB for role cards and nonbasic
lands; otherwise it falls back to the autoregressive Phase 4 output.

Usage
-----
    # Single commander (partial, case-insensitive match)
    python eval_deck.py "Atraxa, Praetors' Voice"
    python eval_deck.py "Krenko" --top 30

    # Aggregate metrics across N random commanders
    python eval_deck.py --stats --n 50

    # Override artifact / checkpoint paths
    python eval_deck.py "Atraxa" \\
        --checkpoint phase4_best \\
        --dataset .\\ingest_cache\\mtg_commanders.pt

Options
-------
NAME
    Commander name (partial, case-insensitive).

--checkpoint NAME
    Checkpoint stem in CHECKPOINT_DIR (default: phase4_best).

--dataset PATH
    Path to mtg_commanders.pt artifact
    (default: ingest_cache\\mtg_commanders.pt relative to repo root).

--top N
    Number of top-scored cards to show in each type group before
    collapsing the rest.  Pass 0 to show all cards.  (default: 0)

--stats
    Run aggregate metrics across --n random commanders and exit.

--n N
    Number of random commanders to sample for --stats (default: 30).

--no-phase5
    Skip Phase 5 assembly; print the raw Phase 4 autoregressive output
    instead.  Useful when DATABASE_URL is unavailable.

Quality metrics
---------------
  Recall        Fraction of the commander's artifact positives that appear
                in the generated deck.  Low recall (<5%) suggests the model
                has collapsed or ignores the training signal.

  Violations    Cards in the generated deck whose color identity is NOT a
                subset of the commander's color identity.  Should be 0.

  Mean sim      Mean pairwise cosine similarity between the encoded deck
                cards (excluding the commander).  Healthy range: 0.15–0.45.
                Values above 0.70 indicate representation collapse.

  Type mix      Card type distribution across the 99-card deck.
"""

from __future__ import annotations

import argparse
import os
import random
import sys
from collections import Counter, defaultdict
from pathlib import Path

import numpy as np
import torch

from train import CardEncoder, DeckConstructor, load_artifact, load_checkpoint, CHECKPOINT_DIR
import deck_builder

DATABASE_URL = (
    os.environ.get("DATABASE_URL", "")
    .replace("postgresql+asyncpg://", "postgresql://")
)

# ── Artifact helpers ──────────────────────────────────────────────────────────


def _build_index(data: dict) -> tuple[list[str], dict[str, int]]:
    card_ids = data["card_ids"]
    id_to_idx = {cid: i for i, cid in enumerate(card_ids)}
    return card_ids, id_to_idx


def _find_commander(query: str, data: dict, id_to_idx: dict) -> str | None:
    """Return card_id for the best case-insensitive name match among commanders."""
    card_meta = data.get("card_meta", {})
    q = query.lower()
    # only consider commanders that appear in the decks list
    commander_ids = {data["card_ids"][d["commander_idx"]] for d in data["decks"]}

    for cid in commander_ids:
        if card_meta.get(cid, {}).get("name", "").lower() == q:
            return cid
    for cid in commander_ids:
        if card_meta.get(cid, {}).get("name", "").lower().startswith(q):
            return cid
    for cid in commander_ids:
        if q in card_meta.get(cid, {}).get("name", "").lower():
            return cid
    return None


_ILLEGAL_TYPE_FRAGMENTS = frozenset({
    "Stickers", "Conspiracy", "Vanguard", "Phenomenon", "Scheme",
})


def _is_commander_legal(type_line: str) -> bool:
    """Return False for card types that are not legal in Commander."""
    return not any(frag in type_line for frag in _ILLEGAL_TYPE_FRAGMENTS)


def _legal_ids(
    commander_id: str,
    card_ids: list[str],
    color_ids: dict[str, frozenset],
    card_meta: dict | None = None,
) -> list[str]:
    """Return all card_ids whose color identity is legal under the commander."""
    cmd_ci = color_ids.get(commander_id, frozenset())
    return [
        cid for cid in card_ids
        if cid in color_ids
        and color_ids[cid] <= cmd_ci
        and cid != commander_id
        and (
            card_meta is None
            or _is_commander_legal(card_meta.get(cid, {}).get("type_line", ""))
        )
    ]


def _known_positives(commander_id: str, data: dict) -> set[str]:
    """Return the set of card_ids that are known positives for this commander."""
    card_ids = data["card_ids"]
    for deck in data["decks"]:
        if card_ids[deck["commander_idx"]] == commander_id:
            return {card_ids[i] for i in deck["card_idxs"]}
    return set()


# ── Encoder projection ────────────────────────────────────────────────────────


def _encode_all(
    emb_matrix: np.ndarray,
    model: DeckConstructor,
    device: torch.device,
    batch_size: int = 512,
) -> torch.Tensor:
    """Project all raw embeddings through card_encoder. Returns (N, D) on CPU."""
    model.eval()
    out = []
    with torch.no_grad():
        for i in range(0, len(emb_matrix), batch_size):
            batch = torch.from_numpy(emb_matrix[i: i + batch_size]).to(device)
            out.append(model.card_encoder(batch).cpu())
    return torch.cat(out, dim=0)


# ── Candidate scoring ─────────────────────────────────────────────────────────


def score_candidates(
    model: DeckConstructor,
    commander_id: str,
    legal_card_ids: list[str],
    all_encoded: torch.Tensor,
    id_to_idx: dict[str, int],
    device: torch.device,
) -> list[str]:
    """Return legal_card_ids sorted by cosine similarity to the commander (descending).

    This is a single-pass scoring without the autoregressive loop — it captures
    what Phase 4 learned as a pure ranking function rather than sequential
    composition.  The resulting ranked list is the input to Phase 5 assembly.
    """
    model.eval()
    all_encoded = all_encoded.to(device)
    with torch.no_grad():
        z_cmd = all_encoded[id_to_idx[commander_id]]              # (D,)
        indices = [id_to_idx[c] for c in legal_card_ids]
        z_legal = all_encoded[torch.tensor(indices, device=device)]  # (C, D)
        scores = (z_legal @ z_cmd).cpu()                          # (C,)
    order = scores.argsort(descending=True).tolist()
    return [legal_card_ids[i] for i in order]


# ── Deck generation ───────────────────────────────────────────────────────────


def generate_deck(
    model: DeckConstructor,
    commander_id: str,
    legal_card_ids: list[str],
    all_encoded: torch.Tensor,
    id_to_idx: dict[str, int],
    deck_size: int = 99,
    device: torch.device = torch.device("cpu"),
) -> list[str]:
    """Autoregressively generate a deck via greedy top-1 selection.

    At each step the model sees [commander, cards selected so far] and scores
    all remaining legal candidates.  The highest-scoring card is appended and
    the process repeats until deck_size cards are chosen.

    When the deck is empty (step 0) the model's learned query_token is used
    in place of a deck sequence — identical to Phase 4 training setup.
    """
    model.eval()
    all_encoded = all_encoded.to(device)

    with torch.no_grad():
        z_cmd = all_encoded[id_to_idx[commander_id]].unsqueeze(0)  # (1, D)

        # Legal candidate pool — pre-indexed
        legal_idx = torch.tensor(
            [id_to_idx[c] for c in legal_card_ids], dtype=torch.long, device=device
        )
        z_legal = all_encoded[legal_idx]  # (C, D)

        # Exclusion mask: True = already chosen, excluded from scoring
        excluded = torch.zeros(len(legal_card_ids), dtype=torch.bool, device=device)

        chosen: list[str] = []

        for step in range(deck_size):
            # Build deck sequence (or query token for empty deck)
            if not chosen:
                deck_seq = model.query_token.to(device)  # (1, 1, D)
            else:
                chosen_vecs = torch.stack(
                    [all_encoded[id_to_idx[c]] for c in chosen]
                ).unsqueeze(0).to(device)  # (1, T, D)
                deck_seq = chosen_vecs

            # Decoder context
            memory = z_cmd.unsqueeze(1)          # (1, 1, D)
            deck_ctx = model.decoder(deck_seq, memory)  # (1, T, D)
            ctx = deck_ctx.mean(dim=1)            # (1, D)

            # Score all legal candidates; mask chosen
            scores = (ctx @ z_legal.T).squeeze(0)  # (C,)
            scores[excluded] = float("-inf")

            best = int(scores.argmax().item())
            chosen.append(legal_card_ids[best])
            excluded[best] = True

    return chosen


# ── Quality metrics ───────────────────────────────────────────────────────────


def _mean_pairwise_sim(card_ids: list[str], all_encoded: torch.Tensor, id_to_idx: dict) -> float:
    """Mean pairwise cosine similarity across all cards in the deck."""
    if len(card_ids) < 2:
        return 0.0
    vecs = torch.stack([all_encoded[id_to_idx[c]] for c in card_ids])  # (N, D)
    # vecs are already L2-normalised (CardEncoder output); sim = vecs @ vecs.T
    sim = vecs @ vecs.T  # (N, N)
    n = len(card_ids)
    # upper triangle, excluding diagonal
    upper = sim.triu(diagonal=1)
    return float(upper.sum() / (n * (n - 1) / 2))


def _type_groups(card_ids: list[str], card_meta: dict) -> dict[str, list[str]]:
    """Group card_ids by primary card type (for display)."""
    groups: dict[str, list[str]] = defaultdict(list)
    order = ["Creature", "Instant", "Sorcery", "Enchantment", "Artifact", "Planeswalker", "Land", "Other"]
    for cid in card_ids:
        tl = card_meta.get(cid, {}).get("type_line", "")
        placed = False
        for t in order[:-1]:
            if t in tl:
                groups[t].append(cid)
                placed = True
                break
        if not placed:
            groups["Other"].append(cid)
    return {t: groups[t] for t in order if groups.get(t)}


def _violation_count(
    card_ids: list[str],
    commander_id: str,
    color_ids: dict[str, frozenset],
) -> int:
    cmd_ci = color_ids.get(commander_id, frozenset())
    return sum(
        1 for cid in card_ids
        if not (color_ids.get(cid, frozenset()) <= cmd_ci)
    )


# ── Display ───────────────────────────────────────────────────────────────────

WIDTH = 72


def _print_deck(
    commander_id: str,
    deck: list[str],
    data: dict,
    all_encoded: torch.Tensor,
    id_to_idx: dict,
    color_ids: dict[str, frozenset],
    top: int,
) -> dict:
    """Print the generated deck and return a dict of quality metrics."""
    card_meta = data.get("card_meta", {})
    known_pos = _known_positives(commander_id, data)

    cmd_meta  = card_meta.get(commander_id, {})
    cmd_name  = cmd_meta.get("name", commander_id)
    cmd_ci    = "".join(sorted(color_ids.get(commander_id, set())))
    cmd_cost  = cmd_meta.get("mana_cost", "—")
    cmd_type  = cmd_meta.get("type_line", "—")

    print("═" * WIDTH)
    print(f"  {cmd_name}")
    print(f"  {cmd_type}")
    print(f"  {cmd_cost}  [{cmd_ci}]")
    print("═" * WIDTH)

    groups = _type_groups(deck, card_meta)
    for type_name, ids in groups.items():
        show = ids if top == 0 else ids[:top]
        hidden = len(ids) - len(show)
        print(f"\n── {type_name} ({len(ids)}) " + "─" * max(0, WIDTH - len(type_name) - 8))
        for cid in show:
            meta = card_meta.get(cid, {})
            name = (meta.get("name") or cid)[:36]
            cost = (meta.get("mana_cost") or "")[:12]
            tl   = (meta.get("type_line") or "")[:24]
            mark = "✓" if cid in known_pos else " "
            print(f"  {mark} {name:<36}  {cost:<12}  {tl}")
        if hidden:
            print(f"    … {hidden} more (pass --top 0 to show all)")

    # Metrics
    mean_sim   = _mean_pairwise_sim(deck, all_encoded, id_to_idx)
    recall_n   = len(set(deck) & known_pos)
    recall_pct = 100.0 * recall_n / max(len(known_pos), 1)
    violations = _violation_count(deck, commander_id, color_ids)
    type_counts = Counter(
        next((t for t in ["Creature", "Instant", "Sorcery", "Enchantment", "Artifact",
                           "Planeswalker", "Land"] if t in card_meta.get(cid, {}).get("type_line", "")),
             "Other")
        for cid in deck
    )

    collapse_tag = "  ⚠ COLLAPSE RISK" if mean_sim > 0.70 else ""
    violation_tag = f"  ⚠ COLOR VIOLATION" if violations > 0 else ""

    print()
    print("─" * WIDTH)
    print("  Quality metrics")
    print("─" * WIDTH)
    print(f"  Known positives in artifact : {len(known_pos)}")
    print(f"  Recall (✓ in deck / known)  : {recall_n} / {len(known_pos)}  ({recall_pct:.1f}%)")
    print(f"  Color-identity violations   : {violations}{violation_tag}")
    print(f"  Mean pairwise sim (encoded) : {mean_sim:.3f}{collapse_tag}")
    print(f"  Similarity range            : healthy 0.15–0.45  |  collapse > 0.70")
    print()
    print("  Type distribution:")
    for t, n in sorted(type_counts.items(), key=lambda x: -x[1]):
        bar = "█" * min(int(n / max(len(deck), 1) * 40), 40)
        print(f"    {t:<14}  {n:3d}  {bar}")
    print("─" * WIDTH)

    return {
        "recall_pct": recall_pct,
        "violations": violations,
        "mean_sim":   mean_sim,
        "type_counts": type_counts,
    }


def _print_breakdown(breakdown: dict, card_meta: dict) -> None:
    """Print the Phase 5 assembly breakdown."""
    def _names(cids: list[str], cap: int = 6) -> str:
        ns = [card_meta.get(c, {}).get("name", c[:8]) for c in cids[:cap]]
        suffix = f" +{len(cids) - cap} more" if len(cids) > cap else ""
        return ", ".join(ns) + suffix if ns else "(none)"

    print()
    print("─" * WIDTH)
    print("  Phase 5 assembly breakdown")
    print("─" * WIDTH)
    print(f"  Forced ramp  ({len(breakdown['forced_ramp'])}): {_names(breakdown['forced_ramp'])}")
    print(f"  Forced lands ({len(breakdown['forced_lands'])}): {_names(breakdown['forced_lands'])}")
    for role, cards in sorted(breakdown["role"].items()):
        print(f"  Role {role:<14} ({len(cards)}): {_names(cards, 3)}")
    curve_counts = {k: len(v) for k, v in breakdown["curve"].items()}
    curve_str = "  ".join(f"CMC{k}×{curve_counts[k]}" for k in sorted(curve_counts, key=lambda x: int(x)))
    print(f"  Curve fill: {curve_str}")
    print(f"  Nonbasic lands ({len(breakdown['nonbasic_land'])})")
    basics_str = "  ".join(f"{deck_builder.COLOR_TO_BASIC.get(c, c)}×{n}"
                           for c, n in breakdown["basic_land"].items())
    print(f"  Basics: {basics_str or '(none)'}")
    print("─" * WIDTH)


# ── Aggregate stats mode ──────────────────────────────────────────────────────


def _run_stats(
    model: DeckConstructor,
    data: dict,
    all_encoded: torch.Tensor,
    id_to_idx: dict,
    color_ids: dict[str, frozenset],
    n: int,
    device: torch.device,
) -> None:
    card_ids  = data["card_ids"]
    card_meta = data.get("card_meta", {})
    all_decks = data["decks"]

    sample = random.sample(all_decks, min(n, len(all_decks)))
    print(f"Running aggregate eval on {len(sample)} commanders…\n")

    recalls, sims, viols = [], [], []

    for i, deck_entry in enumerate(sample, 1):
        commander_id  = card_ids[deck_entry["commander_idx"]]
        cmd_name      = card_meta.get(commander_id, {}).get("name", commander_id)
        legal         = _legal_ids(commander_id, card_ids, color_ids, card_meta)
        deck          = generate_deck(model, commander_id, legal, all_encoded, id_to_idx,
                                      device=device)
        known_pos     = {card_ids[i] for i in deck_entry["card_idxs"]}
        recall        = 100.0 * len(set(deck) & known_pos) / max(len(known_pos), 1)
        sim           = _mean_pairwise_sim(deck, all_encoded, id_to_idx)
        viol          = _violation_count(deck, commander_id, color_ids)

        recalls.append(recall)
        sims.append(sim)
        viols.append(viol)

        flag = ""
        if sim > 0.70:
            flag += " ⚠COLLAPSE"
        if viol > 0:
            flag += f" ⚠{viol}VIOLATIONS"
        print(f"  [{i:3d}/{len(sample)}]  {cmd_name:<42}  recall={recall:5.1f}%  sim={sim:.3f}{flag}")

    print()
    print("═" * WIDTH)
    print("  Aggregate results")
    print("═" * WIDTH)
    print(f"  Commanders evaluated : {len(sample)}")
    print(f"  Mean recall          : {np.mean(recalls):.1f}%  (min {min(recalls):.1f}%  max {max(recalls):.1f}%)")
    print(f"  Mean pairwise sim    : {np.mean(sims):.3f}  (max {max(sims):.3f})")
    collapse_n = sum(1 for s in sims if s > 0.70)
    print(f"  Collapse risk (>0.70): {collapse_n} / {len(sample)}")
    print(f"  Total violations     : {sum(viols)}")
    print("─" * WIDTH)


# ── CLI ───────────────────────────────────────────────────────────────────────


def main() -> None:
    _repo_root = Path(__file__).parent.parent.parent
    default_dataset = str(_repo_root / "ingest_cache" / "mtg_commanders.pt")

    parser = argparse.ArgumentParser(
        description="Phase 4 + Phase 5 deck evaluator",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "commander",
        nargs="?",
        metavar="NAME",
        help="Commander name to generate for (partial, case-insensitive).",
    )
    parser.add_argument(
        "--checkpoint",
        default="phase4_best",
        help="Checkpoint name in CHECKPOINT_DIR (default: phase4_best).",
    )
    parser.add_argument(
        "--dataset",
        default=default_dataset,
        help=f"Path to mtg_commanders.pt artifact (default: {default_dataset}).",
    )
    parser.add_argument(
        "--top",
        type=int,
        default=0,
        metavar="N",
        help="Cards to show per type group; 0 = all (default: 0).",
    )
    parser.add_argument(
        "--stats",
        action="store_true",
        help="Aggregate mode: evaluate --n random commanders and print summary.",
    )
    parser.add_argument(
        "--n",
        type=int,
        default=30,
        metavar="N",
        help="Number of random commanders for --stats (default: 30).",
    )
    parser.add_argument(
        "--no-phase5",
        action="store_true",
        help="Skip Phase 5 assembly; show raw Phase 4 autoregressive output instead.",
    )
    args = parser.parse_args()

    if not args.stats and not args.commander:
        parser.print_help()
        print("\nTip: pass --stats for an aggregate quality summary.")
        sys.exit(0)

    # Phase 5 requires DB; check availability
    use_phase5 = not args.no_phase5
    db_conn = None
    if use_phase5:
        if DATABASE_URL:
            try:
                import psycopg2
                db_conn = psycopg2.connect(DATABASE_URL)
                print(f"DB         : connected (Phase 5 active)")
            except Exception as e:
                print(f"DB         : unavailable ({e}) — falling back to Phase 4 only")
                use_phase5 = False
        else:
            print("DB         : DATABASE_URL not set — falling back to Phase 4 only")
            use_phase5 = False

    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    print(f"Device     : {device}")
    print(f"Checkpoint : {args.checkpoint}")
    print(f"Artifact   : {args.dataset}")
    print()

    # Load artifact
    data = load_artifact(args.dataset)
    card_ids, id_to_idx = _build_index(data)
    emb_matrix = data["embeddings"].numpy().astype(np.float32)
    color_ids: dict[str, frozenset] = {
        cid: frozenset(ci) for cid, ci in data.get("color_identities", {}).items()
    }

    if not data.get("card_meta"):
        print(
            "ERROR: artifact has no card_meta — re-export with a current version of "
            "export_dataset_commanders.py",
            file=sys.stderr,
        )
        sys.exit(1)

    # Load model
    input_dim = emb_matrix.shape[1]
    model = DeckConstructor(input_dim=input_dim).to(device)
    load_checkpoint(model, args.checkpoint, device)

    # Encode all cards once
    print(f"Encoding {len(card_ids)} cards…")
    all_encoded = _encode_all(emb_matrix, model, device)  # (N, D) on CPU
    print()

    if args.stats:
        _run_stats(model, data, all_encoded, id_to_idx, color_ids, args.n, device)
        if db_conn:
            db_conn.close()
        return

    # Single commander
    commander_id = _find_commander(args.commander, data, id_to_idx)
    if commander_id is None:
        print(f"ERROR: no commander found matching '{args.commander}'", file=sys.stderr)
        print("Tip: only commanders present in the artifact's deck entries are searchable.")
        if db_conn:
            db_conn.close()
        sys.exit(1)

    card_meta = data.get("card_meta", {})
    legal = _legal_ids(commander_id, card_ids, color_ids, card_meta)

    if use_phase5 and db_conn:
        # Phase 5 path: score all candidates → deck_builder assembles 99 cards
        print(f"Scoring {len(legal)} legal candidates (Phase 4)…")
        ranked = score_candidates(model, commander_id, legal, all_encoded, id_to_idx, device)

        print("Assembling deck (Phase 5)…")
        color_identity = color_ids.get(commander_id, frozenset())
        try:
            result = deck_builder.build(
                ranked_cards   = ranked,
                commander_id   = commander_id,
                color_identity = color_identity,
                card_meta      = card_meta,
                conn           = db_conn,
            )
            deck = result["deck"]
            breakdown = result["breakdown"]
        except Exception as e:
            print(f"Phase 5 assembly failed: {e}", file=sys.stderr)
            print("Falling back to Phase 4 autoregressive output…")
            deck = generate_deck(model, commander_id, legal, all_encoded, id_to_idx, device=device)
            breakdown = None
        finally:
            db_conn.close()

        print()
        _print_deck(commander_id, deck, data, all_encoded, id_to_idx, color_ids, top=args.top)
        if breakdown:
            _print_breakdown(breakdown, card_meta)
    else:
        # Phase 4 autoregressive fallback
        print(f"Generating deck ({len(legal)} legal candidates, Phase 4 only)…")
        deck = generate_deck(model, commander_id, legal, all_encoded, id_to_idx, device=device)
        if db_conn:
            db_conn.close()
        print()
        _print_deck(commander_id, deck, data, all_encoded, id_to_idx, color_ids, top=args.top)


if __name__ == "__main__":
    main()
