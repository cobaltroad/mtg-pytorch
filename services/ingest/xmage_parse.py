"""XMage source parser — supplement card_abilities with game-engine-verified ability data.

Walks the XMage ``Mage.Sets`` Java source tree, extracts structured ability
information from import statements, and upserts ``card_abilities`` rows tagged
``source='xmage'``.

Unlike the oracle-text regex patterns in ``synergy/``, XMage ability class names
are verified by the Java game engine (they must compile and run correctly), so
they represent a ground-truth signal for ability type.

Usage
-----
Standalone::

    python xmage_parse.py                          # default XMAGE_DIR=/mage
    python xmage_parse.py --xmage-dir /path/to/mage

Pipeline stage::

    docker compose run --rm ingest python pipeline.py --stage tag_abilities_xmage

Environment
-----------
XMAGE_DIR
    Path to the XMage repository root.  Default: ``/mage``.
    The script searches ``{XMAGE_DIR}/Mage.Sets/src/mage/cards/``.
DATABASE_URL
    Async PostgreSQL URL (required).
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import re
from pathlib import Path

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from tqdm import tqdm

log = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

DATABASE_URL = os.environ.get("DATABASE_URL", "")

# ── XMage ability-class → trigger_event mapping ──────────────────────────────
#
# Maps fully-qualified XMage ability class names (short form) to the
# trigger_event strings used in card_abilities.  Only the most common ability
# classes with unambiguous game-event semantics are listed here; unknown
# classes are ignored rather than mapped to a generic event.

ABILITY_CLASS_TO_EVENT: dict[str, str] = {
    # ETB triggers
    "EntersBattlefieldTriggeredAbility":              "creature_etb",
    "EntersBattlefieldControlledTriggeredAbility":    "creature_etb",
    "EntersBattlefieldAllTriggeredAbility":           "enters_battlefield",
    "EntersBattlefieldThisOrAnotherTriggeredAbility": "creature_etb",
    # Landfall
    "LandfallAbility":                                "landfall",
    # Death triggers
    "DiesSourceTriggeredAbility":                     "dies",
    "DiesCreatureTriggeredAbility":                   "dies",
    "DiesAttachedTriggeredAbility":                   "dies",
    # Combat
    "AttacksTriggeredAbility":                        "attacks",
    "AttacksWithCreaturesTriggeredAbility":           "attacks",
    "DealsCombatDamageToAPlayerTriggeredAbility":     "combat_damage",
    # Spellcasting
    "SpellCastControllerTriggeredAbility":            "spell_cast",
    "SpellCastOpponentTriggeredAbility":              "spell_cast",
    # Sacrifice
    "SacrificePermanentTriggeredAbility":             "sacrifice",
    # Draw
    "DrawCardControllerTriggeredAbility":             "spell_draw",
}

# ── XMage effect-class → effect_class string mapping ─────────────────────────
#
# Maps effect import class names to the effect_class column values stored in
# card_abilities.  Provides a secondary signal for what the ability does.

EFFECT_CLASS_TO_EFFECT: dict[str, str] = {
    "DrawCardSourceControllerEffect":   "draw",
    "DrawCardsControllerEffect":        "draw",
    "DrawCardTargetEffect":             "draw",
    "GainLifeEffect":                   "lifegain",
    "CreateTokenEffect":                "create_token",
    "AddCountersSourceEffect":          "counter_add",
    "AddCountersTargetEffect":          "counter_add",
    "DestroyTargetEffect":              "destroy",
    "ExileTargetEffect":                "exile",
    "ReturnToHandTargetEffect":         "bounce",
    "DamageTargetEffect":               "damage",
    "DamagePlayersEffect":              "damage",
    "BoostControlledEffect":            "pump_controlled",
    "BoostSourceEffect":                "self_pump",
    "BoostTargetEffect":                "target_pump",
    "SearchLibraryPutInHandEffect":     "tutor",
    "SearchLibraryPutInPlayEffect":     "tutor",
    "CounterTargetEffect":              "counter_spell",
    "MillCardsTargetEffect":            "mill",
    "MillCardsControllerEffect":        "mill",
}

# ── Name normalisation ────────────────────────────────────────────────────────

_NON_ALNUM = re.compile(r"[^a-z0-9]")
_CAMEL_SPLIT = re.compile(r"(?<=[a-z0-9])(?=[A-Z])")


def _normalize(s: str) -> str:
    """Strip non-alphanumeric chars and lowercase — used for fuzzy card-name lookup."""
    return _NON_ALNUM.sub("", s.lower())


def _camel_to_words(filename_stem: str) -> str:
    """Convert CamelCase XMage filename stem to a space-separated card name.

    Examples::

        "WarrenWarleader"       → "Warren Warleader"
        "SwordsToPlowshares"    → "Swords To Plowshares"
        "EleshNornGrandCenobite"→ "Elesh Norn Grand Cenobite"
    """
    return _CAMEL_SPLIT.sub(" ", filename_stem)


# ── Java file parsing ─────────────────────────────────────────────────────────

_IMPORT_RE = re.compile(
    r"import\s+mage\.abilities\.(common|effects\.common[^;]*)\."
    r"([A-Za-z][A-Za-z0-9_]+)\s*;"
)


def parse_java_file(path: Path) -> tuple[list[str], list[str]]:
    """Return (ability_classes, effect_classes) imported by a Java card file.

    Only ``mage.abilities.common.*`` (ability classes) and
    ``mage.abilities.effects.common.*`` (effect classes) are extracted.
    """
    ability_classes: list[str] = []
    effect_classes: list[str] = []

    text_content = path.read_text(encoding="utf-8", errors="replace")
    for m in _IMPORT_RE.finditer(text_content):
        pkg, cls = m.group(1), m.group(2)
        if pkg == "common":
            ability_classes.append(cls)
        else:
            effect_classes.append(cls)

    return ability_classes, effect_classes


# ── DB helpers ────────────────────────────────────────────────────────────────

async def _load_name_map(db: AsyncSession) -> dict[str, str]:
    """Return {normalized_name: card_id} for all cards in the DB."""
    result = await db.execute(text("SELECT id, name FROM cards WHERE name IS NOT NULL"))
    return {_normalize(row[1]): str(row[0]) for row in result.fetchall()}


# ── Main logic ────────────────────────────────────────────────────────────────

async def tag_abilities_xmage(xmage_dir: Path) -> None:
    """Walk XMage card sources and insert card_abilities rows tagged source='xmage'."""
    cards_dir = xmage_dir / "Mage.Sets" / "src" / "mage" / "cards"
    if not cards_dir.exists():
        log.error("XMage cards directory not found: %s", cards_dir)
        log.error("Set XMAGE_DIR or pass --xmage-dir to point at the XMage repo root.")
        return

    java_files = sorted(cards_dir.rglob("*.java"))
    log.info("Found %d XMage card files in %s", len(java_files), cards_dir)

    engine = create_async_engine(DATABASE_URL, echo=False)
    Session = async_sessionmaker(engine, expire_on_commit=False)

    async with Session() as db:
        name_map = await _load_name_map(db)
    log.info("Loaded %d card names from DB", len(name_map))

    inserted = skipped_no_match = skipped_no_abilities = 0
    batch: list[dict] = []

    for java_file in tqdm(java_files, desc="Parsing XMage sources"):
        ability_classes, effect_classes = parse_java_file(java_file)

        # Map to known events; skip files with no recognised ability classes
        events: list[tuple[str, str, str]] = []  # (trigger_event, ability_class, effect_class)
        for ac in ability_classes:
            event = ABILITY_CLASS_TO_EVENT.get(ac)
            if event is None:
                continue
            # Find the best effect for this file (first match wins)
            effect = next(
                (EFFECT_CLASS_TO_EFFECT[ec] for ec in effect_classes if ec in EFFECT_CLASS_TO_EFFECT),
                None,
            )
            events.append((event, ac, effect or ""))

        if not events:
            skipped_no_abilities += 1
            continue

        # Normalise file stem → card name → DB lookup
        stem = java_file.stem  # e.g. "WarrenWarleader"
        key = _normalize(stem)
        card_id = name_map.get(key)
        if card_id is None:
            skipped_no_match += 1
            continue

        for trigger_event, ability_class, effect_cls in events:
            batch.append({
                "card_id": card_id,
                "ability_type": "triggered",
                "ability_name": ability_class,
                "trigger_event": trigger_event,
                "effect_class": effect_cls or None,
                "raw_text": ability_class,
            })

    log.info(
        "Parsed: %d rows to insert, %d files skipped (no DB match), %d skipped (no known abilities)",
        len(batch), skipped_no_match, skipped_no_abilities,
    )

    if not batch:
        log.warning("No rows to insert — check XMAGE_DIR and DB connectivity.")
        return

    # Bulk upsert in chunks of 1 000.
    # Note: executemany rowcount is unreliable for ON CONFLICT DO NOTHING via asyncpg
    # (returns -1 or a negative aggregate).  We query the final count instead.
    chunk_size = 1_000
    async with Session() as db:
        for i in range(0, len(batch), chunk_size):
            chunk = batch[i : i + chunk_size]
            await db.execute(
                text("""
                    INSERT INTO card_abilities
                        (card_id, ability_type, ability_name, trigger_event, effect_class, raw_text, source)
                    VALUES
                        (:card_id, :ability_type, :ability_name, :trigger_event, :effect_class, :raw_text, 'xmage')
                    ON CONFLICT (card_id, ability_type, ability_name, COALESCE(effect_class, ''))
                    DO NOTHING
                """),
                chunk,
            )
        await db.commit()

    async with Session() as db:
        result = await db.execute(
            text("SELECT COUNT(*) FROM card_abilities WHERE source = 'xmage'")
        )
        xmage_total = result.scalar()

    log.info("XMage tagging complete: %d xmage rows now in card_abilities", xmage_total)


# ── CLI entry point ───────────────────────────────────────────────────────────

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Tag card_abilities from XMage source tree.")
    parser.add_argument(
        "--xmage-dir",
        type=Path,
        default=Path(os.environ.get("XMAGE_DIR", "/mage")),
        help="Path to the XMage repository root (default: /mage or $XMAGE_DIR)",
    )
    args = parser.parse_args()

    if not DATABASE_URL:
        raise SystemExit("DATABASE_URL environment variable is required.")

    asyncio.run(tag_abilities_xmage(args.xmage_dir))
