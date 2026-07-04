"""compute_card_facts stage — persist Layer-1 card facts.

Entrypoint:  python pipeline.py --stage compute_card_facts

Reads every card, runs the pure parser in shared/composition/card_facts.py,
and upserts one row per card into ``card_facts`` (migration 006).  Depends
only on the download stage; safe to re-run any time the parser changes.

MDFC detection needs ``cards.faces``, which is populated by the download
stage — cards downloaded before migration 006 have ``faces IS NULL`` and
simply get ``is_mdfc_land = false`` until download is re-run.
"""
from __future__ import annotations

import json
import logging

from sqlalchemy import text

from composition.card_facts import compute_card_facts as _compute
from stages.db import BATCH_SIZE, Session

log = logging.getLogger(__name__)

_UPSERT = text("""
    INSERT INTO card_facts (
        card_id, generic, has_x, pips, hybrid_pips,
        is_land, is_basic, land_colors, etb_tapped, is_fetch, is_mdfc_land,
        updated_at
    ) VALUES (
        :card_id, :generic, :has_x, :pips, :hybrid_pips,
        :is_land, :is_basic, :land_colors, :etb_tapped, :is_fetch, :is_mdfc_land,
        NOW()
    )
    ON CONFLICT (card_id) DO UPDATE SET
        generic      = EXCLUDED.generic,
        has_x        = EXCLUDED.has_x,
        pips         = EXCLUDED.pips,
        hybrid_pips  = EXCLUDED.hybrid_pips,
        is_land      = EXCLUDED.is_land,
        is_basic     = EXCLUDED.is_basic,
        land_colors  = EXCLUDED.land_colors,
        etb_tapped   = EXCLUDED.etb_tapped,
        is_fetch     = EXCLUDED.is_fetch,
        is_mdfc_land = EXCLUDED.is_mdfc_land,
        updated_at   = NOW()
""")


def _to_row(card_id: str, mana_cost, type_line, oracle_text, produced_mana, faces) -> dict:
    if isinstance(faces, str):  # JSONB may arrive decoded or as raw text
        faces = json.loads(faces)
    facts = _compute(mana_cost, type_line, oracle_text, produced_mana, faces)
    return {
        "card_id":      card_id,
        "generic":      facts.mana.generic,
        "has_x":        facts.mana.has_x,
        "pips":         json.dumps(facts.mana.pips),
        "hybrid_pips":  json.dumps(facts.mana.hybrid),
        "is_land":      facts.land.is_land,
        "is_basic":     facts.land.is_basic,
        "land_colors":  facts.land.land_colors,
        "etb_tapped":   facts.land.etb_tapped,
        "is_fetch":     facts.land.is_fetch,
        "is_mdfc_land": facts.is_mdfc_land,
    }


async def compute_card_facts() -> None:
    async with Session() as db:
        result = await db.execute(text(
            "SELECT id::text, mana_cost, type_line, oracle_text, produced_mana, faces"
            " FROM cards"
        ))
        cards = result.fetchall()

    log.info("Computing card facts for %d cards…", len(cards))
    rows = [_to_row(*c) for c in cards]

    async with Session() as db:
        for i in range(0, len(rows), BATCH_SIZE):
            await db.execute(_UPSERT, rows[i : i + BATCH_SIZE])
            await db.commit()
        log.info("Upserted %d card_facts rows", len(rows))

    n_mdfc_missing = sum(1 for c in cards if c[5] is None)
    if n_mdfc_missing:
        log.warning(
            "%d cards have no faces data (downloaded before migration 006) — "
            "is_mdfc_land defaulted to false; re-run --stage download to fix.",
            n_mdfc_missing,
        )


if __name__ == "__main__":
    import asyncio

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    asyncio.run(compute_card_facts())
