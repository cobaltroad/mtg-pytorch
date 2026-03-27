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
    # ── ETB triggers ─────────────────────────────────────────────────────────
    "EntersBattlefieldTriggeredAbility":              "creature_etb",
    "EntersBattlefieldControlledTriggeredAbility":    "creature_etb",
    "EntersBattlefieldAllTriggeredAbility":           "enters_battlefield",
    "EntersBattlefieldThisOrAnotherTriggeredAbility": "creature_etb",
    "EntersBattlefieldOrAttacksSourceTriggeredAbility": "creature_etb",
    "EntersBattlefieldOrDiesSourceTriggeredAbility":  "creature_etb",
    "EntersBattlefieldOrLeavesSourceTriggeredAbility": "creature_etb",
    "AllyEntersBattlefieldTriggeredAbility":          "creature_etb",

    # ── Landfall ──────────────────────────────────────────────────────────────
    "LandfallAbility":                                "landfall",

    # ── Death triggers ────────────────────────────────────────────────────────
    "DiesSourceTriggeredAbility":                     "dies",
    "DiesCreatureTriggeredAbility":                   "dies",
    "DiesAttachedTriggeredAbility":                   "dies",
    "DiesThisOrAnotherTriggeredAbility":              "dies",
    "PutIntoGraveFromBattlefieldSourceTriggeredAbility": "dies",
    "PutIntoGraveFromBattlefieldAllTriggeredAbility": "dies",
    "DealtDamageAndDiedTriggeredAbility":             "dies",
    # Keyword abilities that implement dies-triggered behaviour
    "UndyingAbility":                                 "dies",
    "PersistAbility":                                 "dies",
    "AfterlifeAbility":                               "dies",

    # ── Combat / attacks ──────────────────────────────────────────────────────
    "AttacksTriggeredAbility":                        "attacks",
    "AttacksWithCreaturesTriggeredAbility":           "attacks",
    "AttacksAllTriggeredAbility":                     "attacks",
    "AttacksOrBlocksTriggeredAbility":                "attacks",
    "AttacksCreatureYouControlTriggeredAbility":      "attacks",
    "AttacksAttachedTriggeredAbility":                "attacks",
    "AttacksAloneControlledTriggeredAbility":         "attacks",
    "AttacksAndIsNotBlockedTriggeredAbility":         "attacks",
    "AttacksWhileSaddledTriggeredAbility":            "attacks",

    # ── Combat damage ─────────────────────────────────────────────────────────
    "DealsCombatDamageToAPlayerTriggeredAbility":     "combat_damage",
    "DealsDamageToAPlayerAllTriggeredAbility":        "combat_damage",
    "OneOrMoreCombatDamagePlayerTriggeredAbility":    "combat_damage",
    "DealsDamageToOpponentTriggeredAbility":          "combat_damage",
    "DealsDamageToAPlayerAttachedTriggeredAbility":   "combat_damage",
    "DealsDamageSourceTriggeredAbility":              "combat_damage",
    "DealsDamageToAPlayerTriggeredAbility":           "combat_damage",

    # ── Spellcasting ──────────────────────────────────────────────────────────
    # SpellCastControllerTriggeredAbility gets a refined trigger_event at parse
    # time via SPELLCAST_FILTER_MAP (body scan).  The default "spell_cast" is
    # only used when no StaticFilters argument is found in the Java body.
    "SpellCastControllerTriggeredAbility":            "spell_cast",
    "SpellCastOpponentTriggeredAbility":              "spell_cast",
    "SpellCastAllTriggeredAbility":                   "spell_cast",
    "MagecraftAbility":                               "spell_cast",
    "CastSecondSpellTriggeredAbility":                "spell_cast",
    # Heroic triggers on spells that target the card
    "HeroicAbility":                                  "spell_cast",

    # ── Sacrifice ─────────────────────────────────────────────────────────────
    "SacrificePermanentTriggeredAbility":             "sacrifice",
    "ExploitCreatureTriggeredAbility":                "sacrifice",

    # ── Draw / card advantage ─────────────────────────────────────────────────
    "DrawCardControllerTriggeredAbility":             "spell_draw",
    "DrawNthCardTriggeredAbility":                    "spell_draw",

    # ── Lifegain ──────────────────────────────────────────────────────────────
    "GainLifeControllerTriggeredAbility":             "lifegain",

    # ── Discard / cycling ─────────────────────────────────────────────────────
    "CycleTriggeredAbility":                          "discard",
    "CycleOrDiscardControllerTriggeredAbility":       "discard",

    # ── Counter placement ─────────────────────────────────────────────────────
    "OneOrMoreCountersAddedTriggeredAbility":         "counter_added",

    # ── Counter growth keywords (adapt_evolve) ────────────────────────────────
    # These keyword ability classes live in mage.abilities.keyword and are
    # captured by the keyword branch of _IMPORT_RE.  No direct XMage class
    # exists for cast_creature_spell, enchantress, or sac_outlet — those rely
    # on oracle-text pattern matching (synergy/events.py, synergy/archetypes.py).
    "EvolveAbility":                                  "adapt_evolve",
    "AdaptAbility":                                   "adapt_evolve",
    "GraftAbility":                                   "adapt_evolve",
    "ModularAbility":                                 "adapt_evolve",
    "RiotAbility":                                    "adapt_evolve",

    # ── Mana abilities (mage.abilities.mana.*) ────────────────────────────────
    # These classes appear on both artifacts and lands.  Non-artifact cards are
    # filtered out in tag_abilities_xmage before the row is written to the DB.
    # effect_class is hardcoded to 'produce_mana' so compute_xmage_effect_synergy
    # groups all mana-rock cards into (mana_rock, produce_mana) peer edges.
    "SimpleManaAbility":                              "mana_rock",
    "ColorlessManaAbility":                           "mana_rock",
    "CommanderColorIdentityManaAbility":              "mana_rock",
    "AnyColorManaAbility":                            "mana_rock",
    "AnyColorLandsProduceManaAbility":                "mana_rock",
    "BlackManaAbility":                               "mana_rock",
    "BlueManaAbility":                                "mana_rock",
    "RedManaAbility":                                 "mana_rock",
    "WhiteManaAbility":                               "mana_rock",
    "GreenManaAbility":                               "mana_rock",
}

# Mana ability classes that should only be written for artifact (non-land) cards.
# tag_abilities_xmage filters these by card_id against the artifact set loaded
# at startup, and overrides effect_class to 'produce_mana' so effect_peer
# grouping works in compute_xmage_effect_synergy.
MANA_ABILITY_CLASSES: frozenset[str] = frozenset({
    "SimpleManaAbility",
    "ColorlessManaAbility",
    "CommanderColorIdentityManaAbility",
    "AnyColorManaAbility",
    "AnyColorLandsProduceManaAbility",
    "BlackManaAbility",
    "BlueManaAbility",
    "RedManaAbility",
    "WhiteManaAbility",
    "GreenManaAbility",
})

# ── SpellCastControllerTriggeredAbility filter → refined trigger_event ────────
#
# XMage uses StaticFilters.FILTER_SPELL_* as the second argument to
# SpellCastControllerTriggeredAbility to restrict which spell types trigger the
# ability.  By scanning the Java body for this argument we can assign a refined
# trigger_event (e.g. "enchantment_cast") instead of the generic "spell_cast".
#
# Regex matches:  new SpellCastControllerTriggeredAbility(effect, StaticFilters.FILTER_SPELL_*, ...)
# The StaticFilters constant is the second constructor argument.

SPELLCAST_FILTER_MAP: dict[str, str] = {
    "FILTER_SPELL_SPIRIT_OR_ARCANE":      "spirit_arcane_cast",
    "FILTER_SPELL_AN_ENCHANTMENT":        "enchantment_cast",
    "FILTER_SPELL_AN_ARTIFACT":           "artifact_cast",
    "FILTER_SPELL_A_NON_CREATURE":        "noncreature_cast",
    "FILTER_SPELL_A_CREATURE":            "creature_cast",
    "FILTER_SPELL_AN_INSTANT_OR_SORCERY": "instant_sorcery_cast",
    "FILTER_SPELL_HISTORIC":              "historic_cast",
}

_SPELLCAST_FILTER_RE = re.compile(r"StaticFilters\.(FILTER_SPELL_\w+)")


# ── XMage effect-class → effect_class string mapping ─────────────────────────
#
# Maps effect import class names to the effect_class column values stored in
# card_abilities.  Provides a secondary signal for what the ability does.

EFFECT_CLASS_TO_EFFECT: dict[str, str] = {
    # ── Draw ──────────────────────────────────────────────────────────────────
    "DrawCardSourceControllerEffect":   "draw",
    "DrawCardsControllerEffect":        "draw",
    "DrawCardTargetEffect":             "draw",
    "DrawCardsTargetEffect":            "draw",

    # ── Graveyard recursion — return to hand (Eternal Witness, Archaeomancer) ─
    "ReturnFromGraveyardToHandTargetEffect":         "regrowth",
    "ReturnFromGraveyardToHandAllEffect":            "regrowth",
    "ReturnFromGraveyardToHandChooseEffect":         "regrowth",

    # ── Graveyard recursion — return to battlefield (Reveillark, Sun Titan) ──
    "ReturnFromGraveyardToBattlefieldTargetEffect":                         "reanimate",
    "ReturnFromGraveyardToBattlefieldAllEffect":                            "reanimate",
    "ReturnFromGraveyardToBattlefieldWithCounterTargetEffect":              "reanimate",
    "ReturnFromGraveyardToBattlefieldUnderOwnerControlTargetEffect":        "reanimate",

    # ── Lifegain ──────────────────────────────────────────────────────────────
    "GainLifeEffect":                   "lifegain",
    "GainLifeControllerEffect":         "lifegain",
    "GainLifeAllEffect":                "lifegain",

    # ── Life drain (Gray Merchant, Kokusho) ───────────────────────────────────
    "LoseLifeOpponentsEffect":          "drain",
    "LoseLifeTargetEffect":             "drain",

    # ── Tokens ────────────────────────────────────────────────────────────────
    "CreateTokenEffect":                "create_token",
    "CreateTokenCopyTargetEffect":      "create_token",
    "CreateTokenCopySourceEffect":      "create_token",

    # ── Counters ──────────────────────────────────────────────────────────────
    "AddCountersSourceEffect":          "counter_add",
    "AddCountersTargetEffect":          "counter_add",
    "AddCountersAllEffect":             "counter_add",

    # ── Removal ───────────────────────────────────────────────────────────────
    "DestroyTargetEffect":              "destroy",
    "DestroyAllEffect":                 "wrath",
    "ExileTargetEffect":                "exile",
    "ExileAllEffect":                   "wrath",

    # ── Bounce ────────────────────────────────────────────────────────────────
    "ReturnToHandTargetEffect":         "bounce",
    "ReturnToHandAllEffect":            "bounce",

    # ── Damage ────────────────────────────────────────────────────────────────
    "DamageTargetEffect":               "damage",
    "DamagePlayersEffect":              "damage",
    "DamageAllEffect":                  "damage",

    # ── Pump ──────────────────────────────────────────────────────────────────
    "BoostControlledEffect":            "pump_controlled",
    "BoostSourceEffect":                "self_pump",
    "BoostTargetEffect":                "target_pump",

    # ── Search / tutor ────────────────────────────────────────────────────────
    "SearchLibraryPutInHandEffect":     "tutor",
    "SearchLibraryPutInPlayEffect":     "reanimate",   # "put onto battlefield" = reanimate bucket
    "BasicLandSearchEffect":            "ramp",
    "LandSearchEffect":                 "ramp",

    # ── Counterspell ──────────────────────────────────────────────────────────
    "CounterTargetEffect":              "counter_spell",

    # ── Mill ──────────────────────────────────────────────────────────────────
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
    r"import\s+mage\.abilities\.(common|keyword|mana|effects\.common[^;]*)\."
    r"([A-Za-z][A-Za-z0-9_]+)\s*;"
)

_ABILITY_PKGS = {"common", "keyword", "mana"}


def parse_java_file(path: Path) -> tuple[list[str], list[str], dict[str, str]]:
    """Return (ability_classes, effect_classes, trigger_event_overrides) for a Java card file.

    Captures ``mage.abilities.common.*`` and ``mage.abilities.keyword.*``
    as ability classes, and ``mage.abilities.effects.common.*`` as effect classes.

    ``trigger_event_overrides`` maps ability class name → refined trigger_event
    for any ability whose trigger_event can be narrowed by a body scan.  The
    caller uses this to override the default from ``ABILITY_CLASS_TO_EVENT``.
    Currently populated by:

    - ``SpellCastControllerTriggeredAbility`` → refined event from the
      ``StaticFilters.FILTER_SPELL_*`` constructor argument (e.g. ``"enchantment_cast"``).

    The dict is empty when no refinements are found.  New body-scan rules can be
    added here without changing the function signature.
    """
    ability_classes: list[str] = []
    effect_classes: list[str] = []

    text_content = path.read_text(encoding="utf-8", errors="replace")
    for m in _IMPORT_RE.finditer(text_content):
        pkg, cls = m.group(1), m.group(2)
        if pkg in _ABILITY_PKGS:
            ability_classes.append(cls)
        else:
            effect_classes.append(cls)

    trigger_event_overrides: dict[str, str] = {}

    # Body scan: SpellCastControllerTriggeredAbility filter argument
    if "SpellCastControllerTriggeredAbility" in ability_classes:
        m = _SPELLCAST_FILTER_RE.search(text_content)
        if m:
            refined = SPELLCAST_FILTER_MAP.get(m.group(1))
            if refined:
                trigger_event_overrides["SpellCastControllerTriggeredAbility"] = refined

    return ability_classes, effect_classes, trigger_event_overrides


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

    # Artifact (non-land) card IDs — used to filter mana ability classes so
    # that basic lands (Forest → GreenManaAbility) and fetch lands are not
    # incorrectly tagged as mana_rock.
    async with Session() as db:
        artifact_ids: set[str] = {
            str(r[0]) for r in (await db.execute(text(
                "SELECT id FROM cards "
                "WHERE type_line ILIKE '%Artifact%' AND type_line NOT ILIKE '%Land%'"
            ))).fetchall()
        }
    log.info("Loaded %d artifact (non-land) card IDs for mana_rock filtering", len(artifact_ids))

    inserted = skipped_no_match = skipped_no_abilities = 0
    batch: list[dict] = []

    for java_file in tqdm(java_files, desc="Parsing XMage sources"):
        ability_classes, effect_classes, trigger_event_overrides = parse_java_file(java_file)

        # All parsed ability classes are stored; ABILITY_CLASS_TO_EVENT provides
        # the trigger_event translation for the co-occurrence path.  Unmapped
        # classes get trigger_event=None so compute_textmatch_synergy (pattern-based) ignores
        # them, while compute_xmage_synergy (compositional path) finds them via
        # ability_name regardless of trigger_event.
        if not ability_classes:
            skipped_no_abilities += 1
            continue

        effect = next(
            (EFFECT_CLASS_TO_EFFECT[ec] for ec in effect_classes if ec in EFFECT_CLASS_TO_EFFECT),
            None,
        )
        events: list[tuple[str | None, str, str]] = []  # (trigger_event, ability_class, effect_class)
        for ac in ability_classes:
            trigger_event = trigger_event_overrides.get(ac) or ABILITY_CLASS_TO_EVENT.get(ac)
            events.append((trigger_event, ac, effect or ""))

        # Normalise file stem → card name → DB lookup
        stem = java_file.stem  # e.g. "WarrenWarleader"
        key = _normalize(stem)
        card_id = name_map.get(key)
        if card_id is None:
            skipped_no_match += 1
            continue

        for trigger_event, ability_class, effect_cls in events:
            if ability_class in MANA_ABILITY_CLASSES:
                # Skip non-artifact cards (lands, creatures) — mana ability
                # classes appear broadly but mana_rock tagging is artifact-only.
                if card_id not in artifact_ids:
                    continue
                # Override effect_class so compute_xmage_effect_synergy groups
                # all mana rocks into (mana_rock, produce_mana) peer edges.
                effect_cls = "produce_mana"
            batch.append({
                "card_id":      card_id,
                "ability_type": "triggered",
                "ability_name": ability_class,
                "trigger_event": trigger_event,   # None for unmapped classes
                "effect_class": effect_cls or None,
                "raw_text":     ability_class,
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
                    DO UPDATE SET trigger_event = EXCLUDED.trigger_event
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
