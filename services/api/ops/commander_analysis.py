"""Commander oracle-text analysis — pure signal extraction, no DB dependency.

Parses a commander's oracle text into structured deckbuilding signals so the UI
can explain *why* the model builds the deck it does, and surface gaps where the
parser has no interpretation (triggering a "consider adding decklists" note).

Design goals:
  - Purely functional: `analyze_commander_oracle_text()` takes strings, returns
    a `CommanderAnalysis`.  No database I/O, no imports from other ops modules.
  - Extensible: add new entries to `RULES_TERM_SIGNALS` to handle new mechanics.
  - Transparent: every signal carries a `confidence` label and the matched phrase.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import NamedTuple

from pydantic import BaseModel


# ── Response models ───────────────────────────────────────────────────────────

class SignalResult(BaseModel):
    signal_type: str    # "tribal" | "mechanic" | "combat" | "counter" | "evasion"
                        # | "death_sac" | "token" | "spellslinger" | "draw"
                        # | "graveyard" | "lifegain" | "unknown"
    label: str          # Human-readable label shown in the UI
    confidence: str     # "high" | "medium" | "low" | "unknown"
    phrase: str         # Matched phrase from oracle text
    boost_applied: bool # Whether this signal changes generation behaviour


class CommanderAnalysis(BaseModel):
    commander_name: str
    color_identity: list[str]
    signals: list[SignalResult]
    gaps: list[str]              # Phrases the parser couldn't interpret
    archetype_hint: str | None   # e.g. "elf tribal + elfball"
    generation_confidence: str   # "high" | "medium" | "low" | "none"
    boost_overrides: list[str]   # Boost keys active for this commander (e.g. ["mana_producers"])
    # Partner fields — None when analyzing a solo commander
    partner_name: str | None = None
    partner_relationship: str | None = None  # "symbiotic" | "additive" | "color_access"


# ── Internal rule representation ──────────────────────────────────────────────

class _RulesTerm(NamedTuple):
    signal_type: str
    label: str
    confidence: str
    boost: str | None   # None → recognized but no boost → appears in gaps too


# ── MTG rules-term dictionary ─────────────────────────────────────────────────
# Each key is the *exact phrase* to search for (case-insensitive substring match).
# boost=None means "recognized concept, but no heuristic boost is implemented" →
# the term is included as a ⚠️ signal AND added to gaps[] to prompt the user.
#
# To extend: add a new key-value pair. That's it.

RULES_TERM_SIGNALS: dict[str, _RulesTerm] = {
    # Mana / ramp mechanics
    "mana ability": _RulesTerm(
        "mechanic", "mana dork / elfball (mana ability = mana-producing activated ability)",
        "high", "mana_producers",
    ),
    # Cycling
    "whenever you cycle": _RulesTerm(
        "mechanic", "cycling matters",
        "high", "cycling",
    ),
    "cycling": _RulesTerm(
        "keyword", "cycling",
        "medium", "cycling",
    ),
    # Dungeon / venture
    "complete a dungeon": _RulesTerm(
        "mechanic", "dungeon completion (venture into the dungeon)",
        "medium", None,
    ),
    "venture into the dungeon": _RulesTerm(
        "mechanic", "venture into the dungeon",
        "medium", None,
    ),
    # Lifegain
    "whenever you gain life": _RulesTerm(
        "lifegain", "lifegain payoff",
        "high", "lifegain",
    ),
    "whenever a player gains life": _RulesTerm(
        "lifegain", "lifegain matters",
        "medium", "lifegain",
    ),
    # Spellslinger keywords
    "prowess": _RulesTerm(
        "keyword", "prowess / spellslinger",
        "high", "spellslinger",
    ),
    "magecraft": _RulesTerm(
        "keyword", "magecraft / spellslinger",
        "high", "spellslinger",
    ),
    "storm": _RulesTerm(
        "keyword", "storm (copies per spell cast this turn)",
        "high", "spellslinger",
    ),
    # Token / wide keywords
    "convoke": _RulesTerm(
        "keyword", "convoke (tap creatures to help cast spells) / token wide",
        "medium", "tokens",
    ),
    "populate": _RulesTerm(
        "keyword", "populate (copy a token) / token wide",
        "high", "tokens",
    ),
    # Ramp / Eldrazi
    "annihilator": _RulesTerm(
        "keyword", "annihilator (Eldrazi aggro / ramp)",
        "high", "ramp",
    ),
    # Energy
    "energy counter": _RulesTerm(
        "mechanic", "energy counter matters",
        "medium", None,
    ),
    # Exile play (e.g. Rocco, Prosper)
    "play it from exile": _RulesTerm(
        "mechanic", "play from exile matters",
        "high", "play_from_exile",
    ),
    "play cards from exile": _RulesTerm(
        "mechanic", "play from exile matters",
        "high", "play_from_exile",
    ),
    "cast it from exile": _RulesTerm(
        "mechanic", "cast from exile matters",
        "high", "play_from_exile",
    ),
    # Food / artifact tokens
    "food token": _RulesTerm(
        "mechanic", "Food artifact tokens (lifegain + sacrifice synergy)",
        "high", "food",
    ),
    "treasure token": _RulesTerm(
        "mechanic", "Treasure token production (ramp)",
        "high", "treasures",
    ),
    "clue token": _RulesTerm(
        "mechanic", "Clue tokens (draw / investigate matters)",
        "high", "clues",
    ),
    # Legendary matters (Gandalf the White, Sisay, Jodah, etc.)
    # Triggered by oracle text that references casting or caring about legendary spells/permanents.
    "legendary spell": _RulesTerm(
        "mechanic", "legendary matters (cast/reward legendary spells)",
        "high", "legendary_matters",
    ),
    "legendary permanent": _RulesTerm(
        "mechanic", "legendary matters (legendary permanent payoff)",
        "high", "legendary_matters",
    ),
    # Weenie / small-creature matters (Delney, Streetwise Lookout; Isamaru, etc.)
    # "power 2 or less" is the canonical MTG phrasing for weenie-matters effects.
    "with power 2 or less": _RulesTerm(
        "mechanic", "weenie / small-creature matters (power 2 or less)",
        "high", "weenie",
    ),
    # Artifact matters (any artifact — Clues, Treasures, Food, etc.)
    # Broader than artifact_creatures; fires when non-creature artifacts are
    # explicitly part of the payoff (Agent of the Iron Throne, Breya, etc.).
    "artifact or creature": _RulesTerm(
        "mechanic", "artifacts matter (any artifact, not just artifact creatures)",
        "high", "artifact_matters",
    ),
    # Artifact creatures matter (Nick Valentine, Breya, Daretti, etc.)
    # More specific than generic "artifact card" — the strategy revolves around
    # artifact creatures specifically (e.g. dying, entering, being sacrificed).
    "artifact creature": _RulesTerm(
        "mechanic", "artifact creatures matter",
        "high", "artifact_creatures",
    ),
    # Equipment / voltron
    "equip": _RulesTerm(
        "keyword", "equip — voltron / Equipment strategy",
        "high", "voltron",
    ),
    # Deathtouch payoff (e.g. Fynn)
    "deathtouch": _RulesTerm(
        "keyword", "deathtouch — wants other deathtouch creatures",
        "high", "deathtouch",
    ),
    # Infect
    "infect": _RulesTerm(
        "keyword", "infect (poison counter win condition)",
        "high", "infect",
    ),
    # Mill
    "mill": _RulesTerm(
        "keyword", "mill (put cards from library into graveyard)",
        "medium", "mill",
    ),
    # Proliferate
    "proliferate": _RulesTerm(
        "keyword", "proliferate (spread counters)",
        "high", "counters",
    ),
    # Keyword counters (e.g. Atraxa)
    "keyword counter": _RulesTerm(
        "mechanic", "keyword counters",
        "medium", "counters",
    ),
    # Card-type matters (e.g. Atraxa Grand Unifier)
    "instant card": _RulesTerm(
        "mechanic", "instant card type matters",
        "medium", "spellslinger",
    ),
    "sorcery card": _RulesTerm(
        "mechanic", "sorcery card type matters",
        "medium", "spellslinger",
    ),
    "artifact card": _RulesTerm(
        "mechanic", "artifact card type matters",
        "medium", "artifacts",
    ),
    "enchantment card": _RulesTerm(
        "mechanic", "enchantment card type matters",
        "medium", "enchantments",
    ),
    "planeswalker card": _RulesTerm(
        "mechanic", "planeswalker card type matters",
        "medium", "planeswalkers",
    ),
    "creature card": _RulesTerm(
        "mechanic", "creature card type matters",
        "medium", "creatures",
    ),
    # Phasing / blink
    "exile and return": _RulesTerm(
        "mechanic", "blink / flicker (exile and return)",
        "medium", "blink",
    ),
    "phase out": _RulesTerm(
        "mechanic", "phasing (phase out / phase in)",
        "medium", None,
    ),
    # Commander-value: explicit "if you control a commander" text on the commander itself
    # (rare, but e.g. some partner / background cards reference this condition)
    "if you control a commander": _RulesTerm(
        "mechanic", "commander in-play payoff (free-cast / bonus while commander present)",
        "high", "commander_value",
    ),
    "as long as you control a commander": _RulesTerm(
        "mechanic", "commander in-play payoff (persistent bonus while commander present)",
        "high", "commander_value",
    ),
}

# ── Pattern-based signal extraction ──────────────────────────────────────────
# Each entry: (signal_type, label, confidence, boost, pattern)

@dataclass
class _PatternSignal:
    signal_type: str
    label: str
    confidence: str
    boost: str | None
    pattern: re.Pattern[str]


_PATTERN_SIGNALS: list[_PatternSignal] = [
    # ── Tribal ────────────────────────────────────────────────────────────────
    _PatternSignal("tribal", "Tribal: Elf", "high", "tribal",
                   re.compile(r"\belf\b|\belves\b", re.I)),
    _PatternSignal("tribal", "Tribal: Zombie", "high", "tribal",
                   re.compile(r"\bzombie(s)?\b", re.I)),
    _PatternSignal("tribal", "Tribal: Goblin", "high", "tribal",
                   re.compile(r"\bgoblin(s)?\b", re.I)),
    _PatternSignal("tribal", "Tribal: Dragon", "high", "tribal",
                   re.compile(r"\bdragon(s)?\b", re.I)),
    _PatternSignal("tribal", "Tribal: Vampire", "high", "tribal",
                   re.compile(r"\bvampire(s)?\b", re.I)),
    _PatternSignal("tribal", "Tribal: Knight", "high", "tribal",
                   re.compile(r"\bknight(s)?\b", re.I)),
    _PatternSignal("tribal", "Tribal: Merfolk", "high", "tribal",
                   re.compile(r"\bmerfolk\b", re.I)),
    _PatternSignal("tribal", "Tribal: Human", "high", "tribal",
                   re.compile(r"\bhuman(s)?\b", re.I)),
    _PatternSignal("tribal", "Tribal: Sliver", "high", "tribal",
                   re.compile(r"\bsliver(s)?\b", re.I)),
    _PatternSignal("tribal", "Tribal: Spirit", "high", "tribal",
                   re.compile(r"\bspirit(s)?\b", re.I)),
    _PatternSignal("tribal", "Tribal: Warrior", "high", "tribal",
                   re.compile(r"\bwarrior(s)?\b", re.I)),
    _PatternSignal("tribal", "Tribal: Wizard", "high", "tribal",
                   re.compile(r"\bwizard(s)?\b", re.I)),
    _PatternSignal("tribal", "Tribal: Cleric", "high", "tribal",
                   re.compile(r"\bcleric(s)?\b", re.I)),
    _PatternSignal("tribal", "Tribal: Rogue", "high", "tribal",
                   re.compile(r"\brogue(s)?\b", re.I)),
    _PatternSignal("tribal", "Tribal: Dinosaur", "high", "tribal",
                   re.compile(r"\bdinosaur(s)?\b", re.I)),
    _PatternSignal("tribal", "Tribal: Ninja", "high", "tribal",
                   re.compile(r"\bninja(s)?\b", re.I)),
    _PatternSignal("tribal", "Tribal: Rat", "high", "tribal",
                   re.compile(r"\brat(s)?\b", re.I)),
    _PatternSignal("tribal", "Tribal: Insect", "high", "tribal",
                   re.compile(r"\binsect(s)?\b", re.I)),
    _PatternSignal("tribal", "Tribal: Changeling (all types)", "high", "tribal",
                   re.compile(r"\bchangeling\b", re.I)),

    # ── Combat ────────────────────────────────────────────────────────────────
    # Extra-trigger doublers — any card that makes abilities fire an additional time
    # (Isshin, Teysa Karlov, Cloud, Panharmonicon-style effects on commanders, etc.).
    _PatternSignal("mechanic", "triggers an additional time", "high", "extra_triggers",
                   re.compile(r"triggers? an additional time", re.I)),
    # Attack-trigger doublers (Isshin, Two Heavens as One) — narrower: only when the
    # "additional time" condition is specifically caused by a creature attacking.
    _PatternSignal("combat", "attack trigger doubling", "high", "attack_triggers",
                   re.compile(r"attacking causes a triggered ability", re.I)),
    # Combat-damage-trigger doublers (Felix Five-Boots) — narrower: only when the
    # "additional time" condition is specifically caused by dealing combat damage to a player.
    _PatternSignal("combat", "combat damage trigger doubling", "high", "combat_damage_triggers",
                   re.compile(r"dealing combat damage to a player causes a triggered ability", re.I)),
    # ETB-trigger doublers (Naban, Dean of Iteration; Gandalf the White, etc.)
    # Pattern is flexible enough to match "entering or leaving ... causes" (Gandalf)
    # as well as the plain "entering causes" form (Naban).
    # Emits two boosts: etb_triggers (the doubling mechanic) and etb_matters
    # (what is entering matters — e.g. Wizard ETBs, artifact ETBs, etc.).
    _PatternSignal("mechanic", "ETB trigger doubling", "high", "etb_triggers",
                   re.compile(r"\bentering\b.{0,60}causes a triggered ability", re.I)),
    _PatternSignal("mechanic", "ETB matters (entering context)", "high", "etb_matters",
                   re.compile(r"\bentering\b.{0,60}causes a triggered ability", re.I)),
    # LTB-trigger doublers (Teysa Karlov, Gandalf the White) — leaving the battlefield
    # or dying causes a triggered ability to trigger an additional time.
    # Emits two boosts: ltb_triggers (the LTB doubling) and extra_triggers (general).
    _PatternSignal("mechanic", "LTB trigger doubling", "high", "ltb_triggers",
                   re.compile(r"(leaving the battlefield|dying).{0,30}causes a triggered ability", re.I)),
    _PatternSignal("mechanic", "LTB trigger doubling (extra triggers context)", "high", "extra_triggers",
                   re.compile(r"(leaving the battlefield|dying).{0,30}causes a triggered ability", re.I)),
    # General LTB / death payoff trigger — "whenever … dies/dying" or the rules-text
    # equivalent "put into a graveyard from the battlefield" are the same event.
    _PatternSignal("mechanic", "LTB / death payoff trigger", "high", "ltb_triggers",
                   re.compile(r"whenever\b.{0,60}(dies|dying|put into a graveyard from the battlefield)\b", re.I)),
    # Artifact matters — any artifact going to graveyard is the payoff
    # (Ich-Tekik, Daretti, etc.). Broader than artifact_creatures.
    _PatternSignal("mechanic", "artifacts matter (artifact LTB payoff)", "high", "artifact_matters",
                   re.compile(r"whenever\b.{0,30}\bartifact\b.{0,40}(dies|put into a graveyard)", re.I)),
    # Artifact matters — any artifact entering is the payoff
    # (Mm'menon, Uthros Exile; Breya; Daretti, etc.).
    _PatternSignal("mechanic", "artifacts matter (artifact ETB payoff)", "high", "artifact_matters",
                   re.compile(r"whenever\b.{0,30}\bartifact\b.{0,40}enters", re.I)),
    # Generic ETB matters — commander rewards permanents/artifacts/creatures entering.
    # (distinct from etb_triggers; this is for commanders with their own ETB payoffs).
    _PatternSignal("mechanic", "ETB matters (permanents entering payoff)", "high", "etb_matters",
                   re.compile(r"whenever (a|an|another|one or more) (artifact|creature|permanent)(s)? (you control )?(enters|enter)\b", re.I)),
    # Equipment / voltron — search for an Equipment card, or ability cares about
    # being equipped (Cloud, Syr Gwyn, etc.).
    _PatternSignal("mechanic", "Equipment tutor / voltron", "high", "voltron",
                   re.compile(r"search.{0,40}equipment card", re.I)),
    _PatternSignal("mechanic", "equipped matters", "high", "voltron",
                   re.compile(r"as long as .{0,30}is equipped", re.I)),
    _PatternSignal("combat", "attack-oriented trigger", "high", None,
                   re.compile(r"\bwhenever\b.{0,60}\battack(s|ed|ing)?\b", re.I)),
    _PatternSignal("combat", "combat damage trigger", "high", None,
                   re.compile(r"\bdeal(s)? combat damage\b", re.I)),
    _PatternSignal("combat", "combat damage to a player", "high", None,
                   re.compile(r"\bdeal(s)?.{0,30}damage to (a |the )?player", re.I)),
    _PatternSignal("combat", "menace — requires two blockers", "high", None,
                   re.compile(r"\bmenace\b", re.I)),
    _PatternSignal("combat", "first strike", "medium", None,
                   re.compile(r"\bfirst strike\b", re.I)),
    _PatternSignal("combat", "double strike", "high", None,
                   re.compile(r"\bdouble strike\b", re.I)),
    _PatternSignal("combat", "trample", "medium", None,
                   re.compile(r"\btrample\b", re.I)),
    _PatternSignal("combat", "deathtouch granted to attackers", "high", "deathtouch",
                   re.compile(r"gain deathtouch.{0,30}(until end of turn|when.{0,30}attack)", re.I)),
    _PatternSignal("combat", "lifelink granted in combat", "medium", None,
                   re.compile(r"gain lifelink.{0,30}(until end of turn|when.{0,30}attack)", re.I)),

    # ── Evasion ───────────────────────────────────────────────────────────────
    _PatternSignal("evasion", "flying", "medium", None,
                   re.compile(r"\bflying\b", re.I)),
    _PatternSignal("evasion", "hexproof", "medium", None,
                   re.compile(r"\bhexproof\b", re.I)),
    _PatternSignal("evasion", "protection from", "medium", None,
                   re.compile(r"\bprotection from\b", re.I)),
    _PatternSignal("evasion", "can't be blocked", "high", None,
                   re.compile(r"\bcan'?t be blocked\b", re.I)),
    _PatternSignal("evasion", "shroud", "medium", None,
                   re.compile(r"\bshroud\b", re.I)),
    _PatternSignal("evasion", "ward", "medium", None,
                   re.compile(r"\bward\b", re.I)),
    _PatternSignal("evasion", "indestructible", "medium", None,
                   re.compile(r"\bindestructible\b", re.I)),

    # ── Counter synergy ───────────────────────────────────────────────────────
    _PatternSignal("counter", "+1/+1 counters", "high", "counters",
                   re.compile(r"\+1/\+1 counter", re.I)),
    _PatternSignal("counter", "-1/-1 counters", "medium", "counters",
                   re.compile(r"-1/-1 counter", re.I)),
    _PatternSignal("counter", "charge counters", "medium", "counters",
                   re.compile(r"\bcharge counter", re.I)),

    # ── Death / sacrifice ─────────────────────────────────────────────────────
    _PatternSignal("death_sac", "death trigger (creature dies)", "high", "aristocrats",
                   re.compile(r"when(ever)?.{0,40}(creature|nontoken).{0,20}dies", re.I)),
    _PatternSignal("death_sac", "sacrifice outlet trigger", "high", "aristocrats",
                   re.compile(r"whenever you sacrifice", re.I)),
    _PatternSignal("death_sac", "sacrifice a creature for effect", "high", "aristocrats",
                   re.compile(r"sacrifice (a|an|one or more) (creature|permanent)", re.I)),

    # ── Token production ──────────────────────────────────────────────────────
    _PatternSignal("token", "creates creature tokens", "high", "tokens",
                   re.compile(r"create (a|an|x|\d+|that many) .{0,30}(creature )?token", re.I)),
    _PatternSignal("token", "token on attack/damage", "high", "tokens",
                   re.compile(r"create (a|an|x|\d+|that many) .{0,30}token.{0,60}(attack|damage|combat)", re.I)),

    # ── Spellslinger ──────────────────────────────────────────────────────────
    _PatternSignal("spellslinger", "instant/sorcery trigger", "high", "spellslinger",
                   re.compile(r"whenever you cast (an? )?(instant|sorcery|noncreature spell)", re.I)),
    _PatternSignal("spellslinger", "each spell cast trigger", "high", "spellslinger",
                   re.compile(r"whenever you cast (your|a|the) (second|third|fourth|next|another)", re.I)),

    # ── Draw / card advantage ─────────────────────────────────────────────────
    _PatternSignal("draw", "draw cards trigger", "high", None,
                   re.compile(r"draw (a |\d+ )?card(s)?", re.I)),
    _PatternSignal("draw", "look at top of library", "medium", None,
                   re.compile(r"look at the top.{0,30}(card|library)", re.I)),
    _PatternSignal("draw", "draw on damage/attack", "high", None,
                   re.compile(r"draw (a |\d+ )?card.{0,60}(whenever|combat|damage|attack)", re.I)),

    # ── Graveyard ─────────────────────────────────────────────────────────────
    _PatternSignal("graveyard", "return from graveyard to battlefield", "high", "graveyard",
                   re.compile(r"return .{0,40} from (your |a |the )?graveyard to (the )?battlefield", re.I)),
    _PatternSignal("graveyard", "cast from graveyard", "high", "graveyard",
                   re.compile(r"from (your |a )?graveyard.{0,30}cast", re.I)),
    _PatternSignal("graveyard", "flashback / unearth", "medium", "graveyard",
                   re.compile(r"\bflashback\b|\bunearth\b", re.I)),

    # ── Lifegain ──────────────────────────────────────────────────────────────
    _PatternSignal("lifegain", "gain life effect", "medium", "lifegain",
                   re.compile(r"\bgain(s)? \d+ life\b", re.I)),
    _PatternSignal("lifegain", "gain life conditional", "medium", "lifegain",
                   re.compile(r"\bgain life\b", re.I)),

    # ── Landfall ──────────────────────────────────────────────────────────────
    _PatternSignal("mechanic", "landfall", "high", "landfall",
                   re.compile(r"\blandfall\b", re.I)),
    _PatternSignal("mechanic", "land enters trigger", "high", "landfall",
                   re.compile(r"whenever (a |one or more )?land(s)?.{0,20}enters", re.I)),

    # ── Aristocrats / death payoffs ───────────────────────────────────────────
    _PatternSignal("death_sac", "when commander enters or leaves", "medium", None,
                   re.compile(r"when(ever)? .{0,30} enters the battlefield", re.I)),
]

# Label used by the low-MV commander signal (step 3c).  Defined here so it can
# be referenced both in the label set and in archetype hint derivation below.
_LOW_MV_LABEL = "low mana-value commander (CMC ≤ 2) — commander-value cards enabled"

# ── Archetype hint derivation ─────────────────────────────────────────────────
# Maps sets of detected boost keys to a human-readable archetype hint.
# Checked in order; first match wins.  More specific combos go first.

_ARCHETYPE_HINTS: list[tuple[set[str], str]] = [
    # ── Legendary matters pairings ────────────────────────────────────────────
    ({"legendary_matters", "etb_triggers", "ltb_triggers"}, "legendary/artifact ETB+LTB trigger doubling (Gandalf-style)"),
    ({"legendary_matters", "etb_triggers"},        "legendary matters + ETB trigger doubling"),
    ({"legendary_matters", "ltb_triggers"},        "legendary matters + LTB trigger doubling"),
    ({"legendary_matters", "extra_triggers"},      "legendary matters + trigger doubling"),
    ({"legendary_matters", "tribal"},              "legendary tribal matters"),
    ({"ltb_triggers", "aristocrats"},              "aristocrats (creature deaths are the primary payoff engine)"),
    ({"ltb_triggers", "tribal"},                   "tribal LTB matters (deaths/exits of that creature type are the payoff)"),
    ({"artifact_matters", "etb_matters"},           "artifact ETB matters (artifacts entering is the payoff)"),
    ({"ltb_triggers", "artifact_matters"},         "artifact LTB matters (any artifact dying/leaving is the payoff)"),
    ({"ltb_triggers", "artifact_creatures"},       "artifact creature death matters (dying artifacts are the engine)"),
    ({"legendary_matters"},                        "legendary matters"),
    # ── ETB / LTB trigger pairings ────────────────────────────────────────────
    ({"etb_triggers", "ltb_triggers"},             "ETB + LTB trigger doubling (entering and leaving both fire twice)"),
    ({"ltb_triggers", "extra_triggers"},           "LTB trigger doubling (leaving-the-battlefield triggers fire twice)"),
    ({"etb_matters", "tribal"},                    "tribal ETB matters (specific creature type entering is the payoff)"),
    ({"extra_triggers", "tribal"},                 "tribal trigger doubling (abilities of that creature type fire twice)"),
    ({"etb_triggers", "extra_triggers"},           "ETB trigger doubling (entering triggers fire twice)"),
    ({"etb_matters", "extra_triggers"},            "ETB matters + extra triggers"),
    ({"etb_matters"},                              "ETB matters / value creatures"),
    ({"combat_damage_triggers", "extra_triggers"}, "combat damage trigger doubling (Felix-style: damage triggers fire twice)"),
    ({"attack_triggers", "extra_triggers"},        "attack trigger doubling (Isshin-style: attack triggers fire twice)"),
    ({"voltron", "extra_triggers"},                "voltron + trigger doubling (equipment abilities fire twice)"),
    ({"weenie", "extra_triggers"},                 "weenie + trigger doubling (small-creature abilities fire twice)"),
    ({"weenie"},                                   "weenie / small-creature matters"),
    ({"extra_triggers"},                           "extra triggers (abilities trigger an additional time)"),
    ({"tribal", "mana_producers"},              "elf tribal + elfball (mana-dork matters)"),
    ({"tribal", "aristocrats"},                 "tribal aristocrats"),
    ({"tribal", "tokens"},                      "tribal token swarm"),
    ({"tribal", "counters"},                    "tribal counters"),
    ({"tribal"},                                "tribal"),
    # Low-MV commander combos (check before generic commander_value)
    ({"commander_value", "spellslinger"},       "low-MV commander + spellslinger (free interaction)"),
    ({"commander_value", "aristocrats"},        "low-MV commander + aristocrats (free disruption)"),
    ({"commander_value", "tokens"},             "low-MV commander + tokens (free interaction)"),
    ({"commander_value", "tribal"},             "low-MV commander + tribal (free interaction)"),
    ({"commander_value"},                       "low-MV commander — free-cast / commander-value staples"),
    ({"mana_producers", "counters"},            "elfball / mana-dork matters + counters"),
    ({"mana_producers"},                        "elfball / mana-dork matters"),
    ({"aristocrats", "tokens"},                 "aristocrats + token sacrifice"),
    ({"aristocrats"},                           "aristocrats"),
    ({"tokens", "go_wide"},                     "go-wide token swarm"),
    ({"tokens"},                                "tokens"),
    ({"play_from_exile", "food"},               "exile-play + Food (lifegain artifacts)"),
    ({"play_from_exile", "treasures"},          "exile-play + Treasure ramp"),
    ({"play_from_exile"},                       "exile-play matters"),
    ({"spellslinger"},                          "spellslinger / storm"),
    ({"counters", "proliferate"},               "proliferate / counter matters"),
    ({"counters"},                              "counters matters"),
    ({"graveyard"},                             "reanimator / graveyard"),
    ({"lifegain"},                              "lifegain payoff"),
    ({"infect"},                                "infect (poison counters)"),
    ({"deathtouch"},                            "deathtouch / poison combat"),
    ({"landfall"},                              "landfall"),
    ({"ramp"},                                  "big mana / Eldrazi ramp"),
    ({"mill"},                                  "mill"),
    ({"voltron"},                               "voltron / equipment"),
]


# ── Main analysis function ────────────────────────────────────────────────────

def analyze_commander_oracle_text(
    oracle_text: str,
    commander_name: str = "Unknown Commander",
    color_identity: list[str] | None = None,
    keywords: list[str] | None = None,
    type_line: str | None = None,
    cmc: float | int | None = None,
) -> CommanderAnalysis:
    """Parse a commander's oracle text into structured deckbuilding signals.

    Parameters
    ----------
    oracle_text:
        The commander's full oracle text (may be empty string).
    commander_name:
        Card name — used in the response object only.
    color_identity:
        Commander's color identity symbols (e.g. ["B", "G"]).
    keywords:
        Printed keyword abilities list from the card data (e.g. ["Flying",
        "Vigilance"]).  These supplement oracle-text scanning.
    type_line:
        The commander's full type line (e.g. "Legendary Creature — Wolf Elf").
        When provided and the card is a Creature, creature subtypes in the type
        line are scanned for tribal signals even if the oracle text does not
        explicitly mention the tribe by name.
    cmc:
        The commander's mana value (converted mana cost).  When provided and
        ≤ 2, a ``commander_value`` signal is emitted — cards like Deflecting
        Swat, Fierce Guardianship, and Loyal Apprentice that care about having
        a commander in play gain maximum value from cheap, frequently-present
        commanders (Rograkh, Yoshimaru, Thrasios, etc.).

    Returns
    -------
    CommanderAnalysis
        Structured result with signals, gaps, archetype hint, and confidence.
    """
    color_identity = color_identity or []
    keywords = keywords or []
    type_line = type_line or ""
    text_lower = oracle_text.lower()

    signals: list[SignalResult] = []
    gaps: list[str] = []
    seen_boosts: set[str] = set()
    seen_labels: set[str] = set()   # deduplicate

    # ── 1. Keyword list from card data (high confidence) ──────────────────────
    for kw in keywords:
        kw_lower = kw.lower()
        # Check against rules-term dict
        if kw_lower in RULES_TERM_SIGNALS:
            term = RULES_TERM_SIGNALS[kw_lower]
            label = term.label
            if label not in seen_labels:
                seen_labels.add(label)
                boost = term.boost or ""
                signals.append(SignalResult(
                    signal_type=term.signal_type,
                    label=label,
                    confidence=term.confidence,
                    phrase=kw,
                    boost_applied=bool(term.boost),
                ))
                if term.boost:
                    seen_boosts.add(term.boost)
                if not term.boost:
                    gaps.append(f'Keyword "{kw}" recognized but no generation boost implemented')

    # ── 2. Rules-term dictionary scan (oracle text) ───────────────────────────
    for phrase, term in RULES_TERM_SIGNALS.items():
        if phrase in text_lower:
            label = term.label
            if label not in seen_labels:
                seen_labels.add(label)
                signals.append(SignalResult(
                    signal_type=term.signal_type,
                    label=label,
                    confidence=term.confidence,
                    phrase=phrase,
                    boost_applied=bool(term.boost),
                ))
                if term.boost:
                    seen_boosts.add(term.boost)
                if not term.boost:
                    gaps.append(
                        f'Recognized MTG rules term "{phrase}" but no generation boost implemented'
                    )

    # ── 3. Pattern-based signal extraction ───────────────────────────────────
    combined = f"{oracle_text}\n{' '.join(keywords)}"
    for ps in _PATTERN_SIGNALS:
        m = ps.pattern.search(combined)
        if m:
            label = ps.label
            if label not in seen_labels:
                seen_labels.add(label)
                signals.append(SignalResult(
                    signal_type=ps.signal_type,
                    label=label,
                    confidence=ps.confidence,
                    phrase=m.group(0).strip(),
                    boost_applied=bool(ps.boost),
                ))
                if ps.boost:
                    seen_boosts.add(ps.boost)

    # ── 3b. Type-line tribal detection ────────────────────────────────────────
    # For commanders that ARE a creature of a given type (e.g. Voja, Jaws of
    # the Conclave — "Legendary Creature — Wolf Elf"), emit a tribal signal for
    # each creature subtype even when the oracle text doesn't enumerate the type
    # by name.  This mirrors the generation-side fix so the UI correctly reflects
    # the tribal boost that will be applied during deck building.
    if type_line and "Creature" in type_line and "\u2014" in type_line:
        creature_subtypes = type_line.split("\u2014", 1)[1].split()
        for subtype in creature_subtypes:
            # Check against the pattern signals to see if this subtype has a known tribal label.
            # Only emit a type-line tribal signal when the tribe is also mentioned in the oracle
            # text or keywords — otherwise a commander like Isshin (Samurai) whose strategy has
            # nothing to do with Samurais would incorrectly receive a tribal signal.
            for ps in _PATTERN_SIGNALS:
                if ps.signal_type == "tribal" and ps.pattern.search(subtype) and ps.pattern.search(combined):
                    if ps.label not in seen_labels:
                        seen_labels.add(ps.label)
                        signals.append(SignalResult(
                            signal_type=ps.signal_type,
                            label=ps.label,
                            confidence=ps.confidence,
                            phrase=subtype,
                            boost_applied=bool(ps.boost),
                        ))
                        if ps.boost:
                            seen_boosts.add(ps.boost)
                    break   # Each subtype should match at most one tribal pattern to avoid duplicate signals

    # ── 3c. Low-MV commander detection ───────────────────────────────────────
    # Commanders with CMC ≤ 2 are reliably in play — they're cheap to cast
    # initially and cheap to recast after removal (command-zone tax is small).
    # This unlocks the full value of "commander-value" cards:
    #   - Free-cast spells (Deflecting Swat, Fierce Guardianship, …)
    #   - Persistent-bonus permanents (Loyal Apprentice, …)
    #   - Legend-mana producers (Mox Amber, …)
    # CMC 0 commanders (Rograkh Son of Rohgahh) are the theoretical maximum;
    # CMC 1 (Yoshimaru, Isamaru) and CMC 2 (Thrasios, Kraum, Lurrus) are the
    # practical sweet spot.  CMC 3+ means the command-zone tax grows each cast,
    # reducing how often the commander is actually in play.
    if cmc is not None and cmc <= 2:
        label = _LOW_MV_LABEL
        if label not in seen_labels:
            seen_labels.add(label)
            cmc_int = int(cmc)
            signals.append(SignalResult(
                signal_type="mechanic",
                label=label,
                confidence="high",
                phrase=f"CMC {cmc_int} commander",
                boost_applied=True,
            ))
            seen_boosts.add("commander_value")

    # ── 4. Gap detection — unrecognized "whenever … / if … / each …" clauses ──
    trigger_phrases = re.findall(
        r"(whenever\s+[^.]{10,80}|if\s+[^,]{10,60},|each\s+[^,]{10,50},)",
        oracle_text,
        re.I,
    )
    for tp in trigger_phrases:
        tp_clean = tp.strip()
        # Consider it unrecognized if none of the known rules-term keys appear in it
        # and none of the pattern signals matched it
        known_hit = any(k in tp_clean.lower() for k in RULES_TERM_SIGNALS)
        pattern_hit = any(ps.pattern.search(tp_clean) for ps in _PATTERN_SIGNALS)
        if not known_hit and not pattern_hit:
            # Only flag genuinely novel-looking constructs (skip trivial fragments)
            if len(tp_clean.split()) > 4:
                gap_msg = f'Unrecognized trigger/condition: "{tp_clean[:80]}"'
                if gap_msg not in gaps:
                    gaps.append(gap_msg)

    # ── 5. Archetype hint derivation ──────────────────────────────────────────
    archetype_hint: str | None = None
    for required_boosts, hint in _ARCHETYPE_HINTS:
        if required_boosts.issubset(seen_boosts):
            archetype_hint = hint
            break

    # ── 6. Generation confidence ──────────────────────────────────────────────
    # Heuristic: more signals with high confidence → better coverage.
    high_count = sum(1 for s in signals if s.confidence == "high")
    gap_count = len(gaps)
    if high_count >= 3 and gap_count == 0:
        generation_confidence = "high"
    elif high_count >= 1 or (len(signals) >= 2 and gap_count <= 1):
        generation_confidence = "medium"
    elif signals:
        generation_confidence = "low"
    else:
        generation_confidence = "none"

    return CommanderAnalysis(
        commander_name=commander_name,
        color_identity=color_identity,
        signals=signals,
        gaps=gaps,
        archetype_hint=archetype_hint,
        generation_confidence=generation_confidence,
        boost_overrides=sorted(seen_boosts),
    )


# ── Partner pair analysis ──────────────────────────────────────────────────────

def _detect_partner_relationship(
    a: CommanderAnalysis,
    b: CommanderAnalysis,
    oracle_text_a: str,
    oracle_text_b: str,
) -> str:
    """Classify the deckbuilding relationship between two partner commanders.

    Rules (checked in order):
    1. Symbiotic  — either oracle text contains "partner with [name]", meaning
                    the two were specifically designed as a pair.
    2. Color access — both have non-empty boost_override sets AND their
                      intersection is empty; each partner pulls the deck in a
                      completely different direction, so one is effectively just
                      providing color identity.
    3. Additive   — default; both are generic value/midrange engines whose
                    signals complement rather than conflict.
    """
    # 1. Named-partner pairs are always symbiotic
    if "partner with" in oracle_text_a.lower() or "partner with" in oracle_text_b.lower():
        return "symbiotic"

    # 2. Disjoint non-empty boost sets → one partner is a color-access passenger
    boosts_a = set(a.boost_overrides)
    boosts_b = set(b.boost_overrides)
    if boosts_a and boosts_b and not (boosts_a & boosts_b):
        return "color_access"

    # 3. Default: additive value engines
    return "additive"


def combine_partner_analyses(
    a: CommanderAnalysis,
    b: CommanderAnalysis,
    oracle_text_a: str,
    oracle_text_b: str,
) -> CommanderAnalysis:
    """Merge two solo CommanderAnalysis objects into a single partner-pair analysis.

    Signals and color identity are always unioned.  Which boost_overrides are
    included depends on the detected relationship:

    - symbiotic:     union of both boost sets — the engine requires both halves.
    - additive:      union of both boost sets — each half contributes independently.
    - color_access:  only the boost set of the partner with *more* boosts is used,
                     since the other partner is not driving deckbuilding goals.

    The archetype hint and generation confidence are re-derived from whichever
    boost set is chosen.
    """
    relationship = _detect_partner_relationship(a, b, oracle_text_a, oracle_text_b)

    # ── Union signals, deduplicating by label ─────────────────────────────────
    seen_labels: set[str] = set()
    merged_signals: list[SignalResult] = []
    for sig in list(a.signals) + list(b.signals):
        if sig.label not in seen_labels:
            seen_labels.add(sig.label)
            merged_signals.append(sig)

    # ── Union gaps ────────────────────────────────────────────────────────────
    merged_gaps: list[str] = list(dict.fromkeys(list(a.gaps) + list(b.gaps)))

    # ── Union color identity ──────────────────────────────────────────────────
    merged_ci: list[str] = list(dict.fromkeys(list(a.color_identity) + list(b.color_identity)))

    # ── Choose boost set by relationship ──────────────────────────────────────
    if relationship == "color_access":
        # Use only the primary commander's boosts (argument `a`).  The caller
        # is responsible for passing the "real" commander as the first argument;
        # the second commander is the color-access passenger whose signals should
        # not drive deck construction.
        active_boosts = set(a.boost_overrides)
    else:
        # symbiotic / additive: union both
        active_boosts = set(a.boost_overrides) | set(b.boost_overrides)

    # ── Re-derive archetype hint from the active boost set ────────────────────
    archetype_hint: str | None = None
    for required_boosts, hint in _ARCHETYPE_HINTS:
        if required_boosts.issubset(active_boosts):
            archetype_hint = hint
            break

    # ── Re-derive generation confidence from merged signals ───────────────────
    high_count = sum(1 for s in merged_signals if s.confidence == "high")
    gap_count  = len(merged_gaps)
    if high_count >= 3 and gap_count == 0:
        generation_confidence = "high"
    elif high_count >= 1 or (len(merged_signals) >= 2 and gap_count <= 1):
        generation_confidence = "medium"
    elif merged_signals:
        generation_confidence = "low"
    else:
        generation_confidence = "none"

    return CommanderAnalysis(
        commander_name=f"{a.commander_name} + {b.commander_name}",
        color_identity=merged_ci,
        signals=merged_signals,
        gaps=merged_gaps,
        archetype_hint=archetype_hint,
        generation_confidence=generation_confidence,
        boost_overrides=sorted(active_boosts),
        partner_name=b.commander_name,
        partner_relationship=relationship,
    )
