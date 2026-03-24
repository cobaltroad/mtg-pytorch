"""
Compositional reasoning training path — data loading.

Supervision signal: functional equivalence from ability tags (Phase 1),
expanded oracle-text synergy patterns (Phase 2), commander role-matching
from oracle text (Phase 3), and role-gap sequencing (Phase 4).

See epic #71 and issues #65-#68 for implementation details.
Checkpoints are prefixed ``comp_phase``.
"""

from __future__ import annotations

import logging
import os
import random
import sys
from collections import defaultdict

import numpy as np
import psycopg2
import psycopg2.extras

log = logging.getLogger(__name__)

CHECKPOINT_PREFIX = "comp_phase"

DATABASE_URL    = os.environ.get("DATABASE_URL", "")
EMBEDDING_MODEL = os.environ.get("EMBEDDING_MODEL", "sentence-transformers/all-mpnet-base-v2")

# Path to services/api so we can import pure oracle-text signal extractors
# (no DB calls — only needs pydantic, which is a trainer dependency).
_API_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..", "services", "api"))


def warm_start_name(phase: int) -> str:
    """Return the checkpoint name to warm-start from for the given phase."""
    return {
        2: "comp_phase1_best",
        3: "comp_phase2_best",
        4: "comp_phase3_best",
    }.get(phase, "")


def _get_conn():
    return psycopg2.connect(DATABASE_URL)


def _load_color_identities(embeddings: dict[str, np.ndarray]) -> dict[str, frozenset]:
    """Return {card_id: frozenset of color letters} for every embedded card."""
    ids = list(embeddings.keys())
    result: dict[str, frozenset] = {}
    with _get_conn() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.DictCursor) as cur:
            cur.execute(
                "SELECT id::text, color_identity FROM cards WHERE id::text = ANY(%s)",
                (ids,),
            )
            for row in cur.fetchall():
                result[row["id"]] = frozenset(row["color_identity"] or [])
    for card_id in ids:
        result.setdefault(card_id, frozenset())
    return result


# ── Phase 2 data ───────────────────────────────────────────────────────────────

def load_synergy_pairs(
    embeddings: dict,
    neg_ratio: int = 3,
    sample: int = 500_000,
    hard_neg_frac: float = 0.5,
    role_demand_sample: int = 100_000,
    combo_sample: int = 200_000,
    commander_value_sample: int = 200_000,
) -> list[tuple[str, str, float]]:
    """Phase 2 compositional: expanded pattern library + XMage-derived pairs.

    Not yet implemented.  See #66.
    """
    raise NotImplementedError(
        "Phase 2 compositional data loading not yet implemented -- see issue #66"
    )


# ── Phase 3 data ───────────────────────────────────────────────────────────────

# Maps commander_analysis boost_overrides → the card roles (from detect_roles)
# that fill those deckbuilding needs.  Empty set = boost has no direct role
# mapping in the current card_abilities schema — skip it.
_BOOST_TO_ROLES: dict[str, set[str]] = {
    # Ramp-family
    "mana_producers":         {"ramp"},
    "ramp":                   {"ramp"},
    "treasures":              {"ramp"},
    "artifacts":              {"ramp"},
    "artifact_matters":       {"ramp"},
    "landfall":               {"ramp"},
    # Draw-family
    "draw":                   {"draw"},
    "cycling":                {"draw"},
    "clues":                  {"draw"},
    "play_from_exile":        {"draw"},
    "self_mill":              {"draw"},
    "self_discard":           {"draw"},
    "spellslinger":           {"draw", "removal"},
    # Removal
    "removal":                {"removal"},
    # Token production
    "tokens":                 {"token"},
    # Aristocrats → wants sac outlets (win_condition) and bodies to sac (token)
    "aristocrats":            {"token", "win_condition"},
    # Win conditions
    "ltb_triggers":           {"win_condition"},
    "counters":               {"win_condition"},
    "lifegain":               {"win_condition"},
    "infect":                 {"win_condition"},
    "punisher":               {"win_condition"},
    "food":                   {"win_condition"},
    # Voltron → equipment provides protection + win condition
    "voltron":                {"protection", "win_condition"},
    # Graveyard → recursion = protection role
    "graveyard":              {"protection"},
    # Boost keys with no direct role counterpart — skip
    "tribal":                 set(),
    "attack_triggers":        set(),
    "etb_triggers":           set(),
    "etb_matters":            set(),
    "extra_triggers":         set(),
    "combat_damage_triggers": set(),
    "legendary_matters":      set(),
    "artifact_creatures":     set(),
    "deathtouch":             set(),
    "mill":                   set(),
    "weenie":                 set(),
    "small_creatures":        set(),
    "blink":                  set(),
    "enchantments":           set(),
    "planeswalkers":          set(),
    "creatures":              set(),
    "commander_value":        set(),
    "multicolor":             set(),
}

# Cap positives per commander to keep GPU batch sizes tractable.
# A commander with "draw" and "removal" needs might match thousands of cards —
# training on all of them at once would be slow and noisy.
_MAX_POSITIVES = 200


def load_decks(embeddings: dict[str, np.ndarray]) -> list[dict]:
    """Phase 3 compositional: commander role-matching triples.

    Ground truth is derived entirely from oracle text — no human decklists
    required.

    Algorithm
    ---------
    1. Query all legal commander cards that appear in the embedding set.
    2. Run ``analyze_commander_oracle_text()`` on each commander to obtain
       ``boost_overrides`` — the deckbuilding signals encoded in oracle text.
    3. Map boosts → needed card roles via ``_BOOST_TO_ROLES``.
    4. Collect every embedded card (within color identity) that fills ≥1
       needed role from the ``card_abilities`` table → *positives*.
    5. Remaining color-legal embedded cards → *negative pool*.

    The returned list of dicts matches the schema consumed by ``DeckDataset``
    and ``train_deck_phase``:

    .. code-block:: python

        {
            "commander_id":      str,
            "card_ids":          list[str],   # positive cards
            "color_identity":    frozenset,
            "legal_neg_indices": np.ndarray,  # indices into list(embeddings)
            "archetype":         str,
        }

    The 70 % role-match / 30 % deck co-occurrence mixing suggested in #67 is
    left as a future enhancement; this initial implementation uses pure
    role-matching.
    """
    # Import oracle-text signal extractor.  It is a pure function (no DB I/O)
    # that lives in services/api/ops — add that directory to sys.path once.
    if _API_DIR not in sys.path:
        sys.path.insert(0, _API_DIR)
    from ops.commander_analysis import analyze_commander_oracle_text  # noqa: PLC0415

    emb_set = set(embeddings.keys())
    all_ids = list(embeddings.keys())
    id_to_idx = {cid: i for i, cid in enumerate(all_ids)}

    # ── Color identities ─────────────────────────────────────────────────────
    log.info("Loading color identities…")
    color_ids = _load_color_identities(embeddings)

    _legal_cache: dict[frozenset, np.ndarray] = {}

    def _legal_indices(cmd_ci: frozenset) -> np.ndarray:
        """Indices (into all_ids) of cards whose color identity ⊆ cmd_ci."""
        if cmd_ci not in _legal_cache:
            idx = np.array(
                [i for i, cid in enumerate(all_ids)
                 if color_ids.get(cid, frozenset()) <= cmd_ci],
                dtype=np.int64,
            )
            _legal_cache[cmd_ci] = idx if len(idx) > 0 else np.arange(len(all_ids), dtype=np.int64)
        return _legal_cache[cmd_ci]

    # ── Legal commanders ─────────────────────────────────────────────────────
    log.info("Loading legal commanders from DB…")
    with _get_conn() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.DictCursor) as cur:
            cur.execute("""
                SELECT c.id::text, c.name, c.oracle_text, c.type_line,
                       c.color_identity, c.keywords
                FROM cards c
                WHERE c.id::text = ANY(%s)
                  AND c.legalities->>'commander' = 'legal'
                  AND (
                    c.type_line ILIKE '%%Legendary Creature%%'
                    OR c.type_line ILIKE '%%Legendary Planeswalker%%'
                    OR c.oracle_text ILIKE '%%can be your commander%%'
                  )
            """, (list(emb_set),))
            commanders = [dict(row) for row in cur.fetchall()]

    log.info("  %d legal commanders in embedding set", len(commanders))

    # ── Card role tags (batch load) ──────────────────────────────────────────
    log.info("Loading card role tags from card_abilities…")
    card_role_map: dict[str, set[str]] = defaultdict(set)
    with _get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT card_id::text, ability_name
                FROM card_abilities
                WHERE ability_type = 'role'
                  AND card_id::text = ANY(%s)
            """, (list(emb_set),))
            for card_id, role in cur.fetchall():
                card_role_map[card_id].add(role)

    log.info("  %d cards have at least one role tag", len(card_role_map))

    # Invert: role → list of card_ids for fast lookup
    role_to_cards: dict[str, list[str]] = defaultdict(list)
    for card_id, roles in card_role_map.items():
        for role in roles:
            role_to_cards[role].append(card_id)

    # ── Build commander → positive cards ─────────────────────────────────────
    log.info("Building role-matching commander decks…")
    decks: list[dict] = []
    skipped_no_boost   = 0
    skipped_no_roles   = 0
    skipped_few_pos    = 0

    for cmd in commanders:
        cmd_id = cmd["id"]
        oracle_text = cmd["oracle_text"] or ""
        type_line   = cmd["type_line"] or ""
        keywords    = list(cmd["keywords"] or [])
        name        = cmd["name"] or ""
        cmd_ci      = frozenset(cmd["color_identity"] or [])

        # Run oracle-text analysis to get deckbuilding signals
        analysis = analyze_commander_oracle_text(
            oracle_text=oracle_text,
            commander_name=name,
            color_identity=sorted(cmd_ci),
            keywords=keywords,
            type_line=type_line,
        )
        boost_overrides: list[str] = analysis.boost_overrides

        if not boost_overrides:
            skipped_no_boost += 1
            continue

        # Map boosts → set of needed card roles
        needed_roles: set[str] = set()
        for boost in boost_overrides:
            needed_roles.update(_BOOST_TO_ROLES.get(boost, set()))

        if not needed_roles:
            skipped_no_roles += 1
            continue

        # Collect all color-legal embedded cards with at least one needed role
        positive_set: set[str] = set()
        for role in needed_roles:
            for card_id in role_to_cards.get(role, []):
                if (
                    card_id != cmd_id
                    and card_id in emb_set
                    and color_ids.get(card_id, frozenset()) <= cmd_ci
                ):
                    positive_set.add(card_id)

        if len(positive_set) < 10:
            skipped_few_pos += 1
            continue

        # Sample positives to keep batch size tractable
        pos_list = list(positive_set)
        if len(pos_list) > _MAX_POSITIVES:
            pos_list = random.sample(pos_list, _MAX_POSITIVES)

        # Negatives: color-legal cards that fill NONE of the needed roles.
        # Exclude the commander itself and all positives.
        legal_all = _legal_indices(cmd_ci)
        positive_idx_set = {id_to_idx[c] for c in positive_set if c in id_to_idx}
        positive_idx_set.add(id_to_idx.get(cmd_id, -1))
        neg_mask = ~np.isin(legal_all, list(positive_idx_set))
        neg_indices = legal_all[neg_mask]
        if len(neg_indices) == 0:
            neg_indices = legal_all  # fallback: all legal cards

        decks.append({
            "commander_id":      cmd_id,
            "card_ids":          pos_list,
            "color_identity":    cmd_ci,
            "legal_neg_indices": neg_indices,
            "archetype":         analysis.archetype_hint or "unknown",
        })

    log.info(
        "Built %d role-matching commander decks  "
        "(%d skipped: no boosts, %d: no roles, %d: <10 positives)",
        len(decks), skipped_no_boost, skipped_no_roles, skipped_few_pos,
    )
    return decks


# ── Phase 4 data ───────────────────────────────────────────────────────────────

def load_synergy_positions(
    decks: list[dict],
    embeddings: dict[str, np.ndarray],
    combo_weight: float = 3.0,
    ability_weight: float = 2.0,
    tribal_weight: float = 1.5,
    synergy_limit_per_commander: int = 300,
) -> list[dict]:
    """Phase 4 compositional: role-gap sequencing positions.

    Not yet implemented.  See #68.
    """
    raise NotImplementedError(
        "Phase 4 compositional synergy positions not yet implemented -- see issue #68"
    )


def load_synergy_positions_global(
    embeddings: dict[str, np.ndarray],
    ability_weight: float = 2.0,
    tribal_weight: float = 1.5,
    synergy_limit_per_commander: int = 300,
) -> list[dict]:
    """Phase 4 compositional: global role-gap positions (all legal commanders).

    Not yet implemented.  See #68.
    """
    raise NotImplementedError(
        "Phase 4 compositional global positions not yet implemented -- see issue #68"
    )
