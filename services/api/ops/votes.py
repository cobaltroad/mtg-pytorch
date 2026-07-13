"""Vote capture on generated decks — feedback loop phase 1 (#180).

Design: docs/feedback-loop-design.md.  'fit' votes become supervised
within-slot ranking labels at export (#181); 'slot' votes are pool-SQL
dispute reports (#182).  Votes are keyed to the deck's lead commander —
for partner decks that means the pair's votes aggregate under the lead
(acceptable single-operator simplification, revisit with #183).
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

log = logging.getLogger(__name__)

_UPSERT = text("""
    INSERT INTO card_votes (commander_id, card_id, vote, kind, slot, deck_ref)
    VALUES (CAST(:commander_id AS uuid), CAST(:card_id AS uuid),
            :vote, :kind, :slot, :deck_ref)
    ON CONFLICT (commander_id, card_id, kind, deck_ref)
    DO UPDATE SET vote = EXCLUDED.vote, slot = EXCLUDED.slot,
                  created_at = NOW()
""")


async def apply_deck_votes(
    db: AsyncSession,
    filename: str,
    votes: list,
    save_dir: Path,
) -> dict:
    """Persist votes against a saved generated deck.

    Each vote: {card_name, vote (+1/-1), kind ('fit'|'slot')}.  Card names
    are resolved against the deck JSON (for slot context) and the cards
    table (for ids); unknown names are reported, not fatal.
    """
    path = save_dir / Path(filename).name
    if not path.exists():
        raise LookupError(f"Generated deck {filename!r} not found")
    deck = json.loads(path.read_text())

    commander_row = (await db.execute(
        text("SELECT id::text FROM cards WHERE oracle_id = CAST(:oid AS uuid) LIMIT 1"),
        {"oid": deck["commander"]["oracle_id"]},
    )).fetchone()
    if commander_row is None:
        raise LookupError("Deck commander not found in cards table")
    commander_id = commander_row[0]

    slot_by_name = {c["name"]: c.get("slot") for c in deck.get("cards", [])}

    stored, unknown = 0, []
    for v in votes:
        name = v.card_name
        if name not in slot_by_name:
            unknown.append(name)
            continue
        card_row = (await db.execute(
            text("SELECT id::text FROM cards WHERE name = :n LIMIT 1"), {"n": name}
        )).fetchone()
        if card_row is None:
            unknown.append(name)
            continue
        await db.execute(_UPSERT, {
            "commander_id": commander_id,
            "card_id": card_row[0],
            "vote": int(v.vote),
            "kind": v.kind,
            "slot": slot_by_name.get(name),
            "deck_ref": Path(filename).name,
        })
        stored += 1
    await db.commit()
    return {"stored": stored, "unknown_cards": unknown}


async def aggregate_votes(db: AsyncSession, oracle_id: str) -> list[dict]:
    """Per-card vote aggregates for a commander (all kinds)."""
    rows = await db.execute(
        text("""
            SELECT c.name,
                   v.kind,
                   SUM(v.vote)  AS score,
                   COUNT(*)     AS n,
                   MAX(v.slot)  AS slot
            FROM card_votes v
            JOIN cards cmd ON cmd.id = v.commander_id
            JOIN cards c   ON c.id = v.card_id
            WHERE cmd.oracle_id = CAST(:oid AS uuid)
            GROUP BY c.name, v.kind
            ORDER BY v.kind, score DESC, c.name
        """),
        {"oid": oracle_id},
    )
    return [
        {"card": r[0], "kind": r[1], "score": int(r[2]), "votes": int(r[3]), "slot": r[4]}
        for r in rows.fetchall()
    ]
