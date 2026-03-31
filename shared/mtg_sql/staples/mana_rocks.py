"""Mana-rock staple SQL — artifact mana producers for any Commander deck.

Covers non-land artifacts that tap to produce mana:
  Sol Ring, Arcane Signet, Talisman cycle, Signet cycle,
  Fellwar Stone, Mind Stone, Thought Vessel, Chromatic Lantern,
  Commander's Sphere, Mox Diamond, Chrome Mox, etc.

Deliberately excludes:
  - Artifact Lands (Seat of the Synod, Ancient Den, …) — those belong
    in the manabase category.
  - Equipment, vehicles, and other non-mana artifacts — matched by
    requiring an oracle-text tap-for-mana clause.
  - Mana dorks (creature mana producers) — those are in ramp.py.

Used by:
  synergy/__init__.py  PRODUCER_MAP['mana_rock']      (ability_trigger edges)
  synergy/xmage.py     XMAGE_PRODUCER_MAP mana classes (xmage_ability_trigger edges)
"""

from __future__ import annotations

# Oracle-text regex: {T} followed (on the same line) by "Add {", "Add one mana",
# or "Add mana".  The double-percent escaping is for psycopg2 callers; SQLAlchemy
# text() passes %% through to PostgreSQL where ILIKE treats it identically to %.
SQL: str = (
    "type_line ILIKE '%%Artifact%%' "
    "AND type_line NOT ILIKE '%%Land%%' "
    "AND oracle_text ~* '\\{T\\}.*[Aa]dd(?: \\{| one mana| mana)'"
)
