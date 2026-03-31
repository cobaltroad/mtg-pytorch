"""
Decklist browser operations.

Exposes human-imported decks for browsing and drives the role-annotation
feedback loop: fetching a deck triggers role-tagging of its cards (writing
to card_abilities).
"""

from __future__ import annotations

import logging
from collections import Counter

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from ops.card_roles import tag_card_roles, get_card_roles, tag_commander_archetypes
from ops.import_utils import detect_archetype

log = logging.getLogger(__name__)

_DECK_LIST_SQL = """
    SELECT
        d.id::text            AS deck_id,
        d.source,
        d.created_at,
        d.metadata,
        c.oracle_id::text     AS commander_oracle_id,
        c.name                AS commander_name,
        c.type_line           AS commander_type_line,
        c.color_identity      AS commander_colors,
        array_length(d.card_ids, 1) AS card_count
    FROM decks d
    JOIN cards c ON c.id = d.commander_id
    ORDER BY d.created_at DESC
"""

_DECK_BY_ID_SQL = """
    SELECT
        d.id::text            AS deck_id,
        d.source,
        d.created_at,
        d.metadata,
        c.oracle_id::text     AS commander_oracle_id,
        c.id::text            AS commander_id,
        c.name                AS commander_name,
        c.type_line           AS commander_type_line,
        c.oracle_text         AS commander_oracle_text,
        c.color_identity      AS commander_colors,
        d.card_ids
    FROM decks d
    JOIN cards c ON c.id = d.commander_id
    WHERE d.id = CAST(:deck_id AS uuid)
"""

_CARDS_BY_IDS_SQL = """
    SELECT
        id::text,
        oracle_id::text,
        name,
        type_line,
        mana_cost,
        cmc,
        oracle_text,
        keywords
    FROM cards
    WHERE id = ANY(CAST(:ids AS uuid[]))
    ORDER BY name
"""


async def list_decks(db: AsyncSession) -> list[dict]:
    """Return all human-imported decks with commander info and card count."""
    rows = await db.execute(text(_DECK_LIST_SQL))
    result = []
    for r in rows:
        m = r.metadata or {}
        result.append({
            "deck_id":              r.deck_id,
            "source":               r.source,
            "created_at":           r.created_at.isoformat() if r.created_at else None,
            "deck_name":            m.get("deck_name") or "",
            "commander_oracle_id":  r.commander_oracle_id,
            "commander_name":       r.commander_name,
            "commander_type_line":  r.commander_type_line,
            "commander_colors":     list(r.commander_colors or []),
            "card_count":           r.card_count or 0,
        })
    return result


async def get_deck_with_roles(db: AsyncSession, deck_id: str) -> dict | None:
    """Fetch a single deck and annotate each card with role tags.

    Side effects on first call (idempotent thereafter):
    1. Writes role tags to card_abilities for any untagged card.
    """
    deck_row = (await db.execute(text(_DECK_BY_ID_SQL), {"deck_id": deck_id})).fetchone()
    if deck_row is None:
        return None

    # ── Commander archetype detection (idempotent, respects user overrides) ──
    _meta_now        = deck_row.metadata or {}
    _arch_overrides  = _meta_now.get("archetype_overrides", {})

    archetypes = await tag_commander_archetypes(
        db,
        card_id=deck_row.commander_id,
        oracle_text=deck_row.commander_oracle_text or "",
        type_line=deck_row.commander_type_line or "",
    )
    # Apply user overrides: dissuaded (-1) archetypes are suppressed from output
    archetypes = [a for a in archetypes if _arch_overrides.get(a, 1) > 0]

    card_ids: list[str] = [str(cid) for cid in (deck_row.card_ids or [])]
    if not card_ids:
        return _format_deck(deck_row, [], archetypes=archetypes)

    cards_rows = (await db.execute(
        text(_CARDS_BY_IDS_SQL),
        {"ids": card_ids},
    )).fetchall()

    # ── Role-tag each card (idempotent INSERT … ON CONFLICT DO NOTHING) ───────
    role_counts: Counter[str] = Counter()
    cards_out: list[dict] = []
    card_details_for_archetype: list[dict] = []  # fed into detect_archetype()

    for row in cards_rows:
        cid         = row[0]
        oracle_id   = row[1]
        name        = row[2]
        type_line   = row[3] or ""
        mana_cost   = row[4]
        cmc         = row[5]
        oracle_text = row[6] or ""
        keywords    = list(row[7] or [])

        # Tag and/or retrieve existing role annotations
        existing = await get_card_roles(db, cid)
        if not existing:
            new_roles = await tag_card_roles(db, cid, oracle_text, type_line, keywords)
            roles = [{"role": r, "effect_class": ec} for r, ec in new_roles]
        else:
            roles = existing

        for r in roles:
            role_counts[r["role"]] += 1

        cards_out.append({
            "card_id":     cid,
            "oracle_id":   oracle_id,
            "name":        name,
            "type_line":   type_line,
            "mana_cost":   mana_cost,
            "cmc":         cmc,
            "oracle_text": oracle_text,
            "roles":       roles,      # list[{role, effect_class}]
        })
        card_details_for_archetype.append({
            "oracle_text": oracle_text,
            "type_line":   type_line,
            "cmc":         cmc,
            "keywords":    keywords,
        })

    # ── Deck-composition archetype detection ──────────────────────────────────
    # Re-run on every browse so that updated heuristics are applied to existing
    # decks (e.g. after backfill_roles.py re-processes them).  Preserves any
    # user votes stored in metadata['archetype_overrides'].
    arch_meta = detect_archetype(card_details_for_archetype)

    # ── Update deck metadata with role counts + archetypes ────────────────────
    try:
        m = dict(deck_row.metadata or {})
        m["role_counts"]    = dict(role_counts)
        m["archetypes"]     = archetypes
        # Deck-composition fields (overwritten on every browse for freshness)
        m["archetype"]      = arch_meta["archetype"]
        m["win_conditions"] = arch_meta["win_conditions"]
        m["avg_cmc"]        = arch_meta["avg_cmc"]
        await db.execute(text("""
            UPDATE decks SET metadata = CAST(:meta AS jsonb) WHERE id = CAST(:deck_id AS uuid)
        """), {"meta": __import__("json").dumps(m), "deck_id": deck_id})
        await db.commit()
    except Exception as exc:
        log.warning("metadata role_counts update failed: %s", exc)

    return _format_deck(deck_row, cards_out, role_counts=dict(role_counts), archetypes=archetypes,
                        arch_meta=arch_meta)


_VOTE_PROMOTE    = 2.0   # score multiplier for promoted role/archetype
_VOTE_DISSUADE   = 0.0   # score multiplier for dissuaded role (removes edge)


async def apply_votes(db: AsyncSession, deck_id: str, votes: list) -> dict:
    """Persist user votes in decks.metadata['votes'].

    Each vote is a VoteEntry-like object with: card_id (opt), role (opt),
    archetype (opt), vote (+1 / -1).

    Vote keys:
      card_role:<card_id>:<role>   → card-level role vote
      archetype:<arch>             → deck-level archetype vote
    """
    row = (await db.execute(
        text("SELECT metadata FROM decks WHERE id = CAST(:deck_id AS uuid)"),
        {"deck_id": deck_id},
    )).fetchone()
    if row is None:
        return {"ok": False, "message": "Deck not found"}

    m = dict(row[0] or {})
    vote_store: dict[str, int] = m.get("votes", {})

    for v in votes:
        if v.card_id and v.role:
            key = f"card_role:{v.card_id}:{v.role}"
        elif v.archetype:
            key = f"archetype:{v.archetype}"
        else:
            continue
        vote_store[key] = int(v.vote)

    m["votes"] = vote_store
    await db.execute(
        text("UPDATE decks SET metadata = CAST(:meta AS jsonb) WHERE id = CAST(:deck_id AS uuid)"),
        {"meta": __import__("json").dumps(m), "deck_id": deck_id},
    )
    await db.commit()
    return {"ok": True, "votes_stored": len(vote_store)}


async def amend_with_votes(db: AsyncSession, deck_id: str) -> dict | None:
    """Re-run analysis and apply stored votes to card_abilities and archetypes.

    Promoted roles  (+1) → confirm role tag in card_abilities
    Dissuaded roles (-1) → remove role tag from card_abilities
    Promoted archetypes  → add archetype to commander's archetype tags
    Dissuaded archetypes → remove archetype from commander's archetype tags

    Returns the updated deck analysis.
    """
    row = (await db.execute(text("""
        SELECT d.metadata, d.commander_id::text, c.name AS cname
        FROM decks d JOIN cards c ON c.id = d.commander_id
        WHERE d.id = CAST(:deck_id AS uuid)
    """), {"deck_id": deck_id})).fetchone()
    if row is None:
        return None

    m           = dict(row[0] or {})
    commander_id = row[1]
    vote_store: dict[str, int] = m.get("votes", {})

    # Apply card-role votes to card_abilities
    for key, vote_val in vote_store.items():
        if key.startswith("card_role:"):
            _, card_id, role = key.split(":", 2)
            if vote_val > 0:
                # Promote: confirm the role tag in card_abilities
                await db.execute(text("""
                    INSERT INTO card_abilities
                        (card_id, ability_type, ability_name, trigger_event, effect_class, raw_text)
                    VALUES
                        (CAST(:card_id AS uuid), 'role', :role, NULL, 'user_vote', 'user promoted')
                    ON CONFLICT (card_id, ability_type, ability_name, effect_class) DO NOTHING
                """), {"card_id": card_id, "role": role})
            else:
                # Dissuade: remove the role tag
                await db.execute(text("""
                    DELETE FROM card_abilities
                    WHERE card_id = CAST(:card_id AS uuid)
                      AND ability_type = 'role'
                      AND ability_name = :role
                """), {"card_id": card_id, "role": role})

        elif key.startswith("archetype:"):
            archetype = key.split(":", 1)[1]
            if vote_val > 0:
                await db.execute(text("""
                    INSERT INTO card_abilities
                        (card_id, ability_type, ability_name, trigger_event, effect_class, raw_text)
                    VALUES
                        (CAST(:commander_id AS uuid), 'archetype', :archetype, NULL, 'user_vote', 'user promoted')
                    ON CONFLICT (card_id, ability_type, ability_name, effect_class) DO NOTHING
                """), {"commander_id": commander_id, "archetype": archetype})
            else:
                await db.execute(text("""
                    DELETE FROM card_abilities
                    WHERE card_id = CAST(:commander_id AS uuid)
                      AND ability_type = 'archetype'
                      AND ability_name = :archetype
                """), {"commander_id": commander_id, "archetype": archetype})

    await db.commit()

    # Persist archetype overrides so re-runs of get_deck_with_roles respect them
    arch_overrides: dict[str, int] = m.get("archetype_overrides", {})
    for key, vote_val in vote_store.items():
        if key.startswith("archetype:"):
            arch_overrides[key.split(":", 1)[1]] = vote_val
    m["amended"] = True
    m["archetype_overrides"] = arch_overrides
    await db.execute(
        text("UPDATE decks SET metadata = CAST(:meta AS jsonb) WHERE id = CAST(:deck_id AS uuid)"),
        {"meta": __import__("json").dumps(m), "deck_id": deck_id},
    )
    await db.commit()

    # Return refreshed deck view
    return await get_deck_with_roles(db, deck_id)


def _format_deck(
    deck_row,
    cards: list[dict],
    role_counts: dict | None = None,
    archetypes: list[str] | None = None,
    arch_meta: dict | None = None,
) -> dict:
    m = deck_row.metadata or {}
    result = {
        "deck_id":             deck_row.deck_id,
        "source":              deck_row.source,
        "created_at":          deck_row.created_at.isoformat() if deck_row.created_at else None,
        "deck_name":           m.get("deck_name") or "",
        "commander_oracle_id": deck_row.commander_oracle_id,
        "commander_name":      deck_row.commander_name,
        "commander_type_line": deck_row.commander_type_line,
        "commander_colors":    list(deck_row.commander_colors or []),
        "archetypes":          archetypes if archetypes is not None else m.get("archetypes", []),
        "role_counts":         role_counts or m.get("role_counts", {}),
        "cards":               cards,
    }
    if arch_meta is not None:
        result["archetype"]      = arch_meta["archetype"]
        result["win_conditions"] = arch_meta["win_conditions"]
        result["avg_cmc"]        = arch_meta["avg_cmc"]
    else:
        result["archetype"]      = m.get("archetype", "unknown")
        result["win_conditions"] = m.get("win_conditions", [])
        result["avg_cmc"]        = m.get("avg_cmc", 0.0)
    return result
