"""Event-based synergy patterns and producer SQL fragments.

Covers the core game-event triggers that span all deck archetypes:
ETB (enters-the-battlefield) sub-types, creature/permanent death, attacks,
spell casting, phase transitions, and the common mid-range event types:
landfall, discard, token creation, counter placement, combat damage, and
sacrifice.  Also includes activated-ability markers (tap, sacrifice-as-cost).
"""

from __future__ import annotations

# ── Trigger patterns ──────────────────────────────────────────────────────────

TRIGGER_PATTERNS: list[tuple[str, str, str]] = [
    # ── ETB sub-types (most specific first so distinct tags are recorded) ─────
    (
        r"when(ever)?\s+(a |another )?nontoken creature.{0,30}enters the battlefield",
        "Nontoken creature ETB",
        "nontoken_etb",
    ),
    (
        r"when(ever)?\s+(a |another )?creature.{0,30}enters the battlefield",
        "Creature ETB trigger",
        "creature_etb",
    ),
    (
        r"when(ever)?\s+.{0,30}artifact.{0,30}enter",
        "Artifact ETB trigger",
        "artifact_etb",
    ),
    # Generic ETB catch-all (also matches enchantments, planeswalkers, etc.)
    (r"when(ever)?\s+.{0,60}enters the battlefield", "ETB trigger", "enters_battlefield"),

    # ── Creature death ────────────────────────────────────────────────────────
    # Nontoken creature dies: the most common Aristocrats / sacrifice payoff template
    (
        r"when(ever)?\s+(a |another )?nontoken creature.{0,30}dies",
        "Nontoken dies trigger",
        "nontoken_dies",
    ),
    # Generic dies trigger (any creature)
    (r"when(ever)?\s+.{0,60}dies", "Dies trigger", "dies"),

    # ── Combat ────────────────────────────────────────────────────────────────
    (r"when(ever)?\s+.{0,60}attacks", "Attack trigger", "attacks"),

    # ── Spellcasting ──────────────────────────────────────────────────────────
    # Explicit instant/sorcery triggers + magecraft keyword
    (
        r"when(ever)?\s+(you |a player |an opponent )cast.{0,10}(noncreature|instant or sorcery|a spell)",
        "Cast trigger",
        "spell_cast",
    ),
    (r"\bmagecraft\b", "Magecraft", "spell_cast"),

    # ── Phase ─────────────────────────────────────────────────────────────────
    # Drop "combat" here — ambiguous with the attacks trigger above
    (
        r"at the beginning of (your|each player's|each)?\s*(upkeep|end step)",
        "Phase trigger",
        "phase_begin",
    ),

    # ── Common event types ────────────────────────────────────────────────────
    (r"when(ever)?\s+a land enters", "Landfall trigger", "landfall"),
    (r"when(ever)?\s+(you |a player |an opponent )discard", "Discard trigger", "discard"),
    (r"when(ever)?\s+(you )?create.{0,30}token", "Token creation trigger", "token_creation"),
    (r"when(ever)?\s+.{0,40}(counter|counters).{0,20}(placed|put) on", "Counter trigger", "counter_added"),
    (
        r"when(ever)?\s+.{0,50}deals? (combat )?damage to (a player|an opponent|you)",
        "Combat damage trigger",
        "combat_damage",
    ),
    (r"when(ever)?\s+(you )?sacrifice", "Sacrifice trigger", "sacrifice"),

]

# ── Producer map ──────────────────────────────────────────────────────────────

PRODUCER_MAP: dict[str, str] = {
    # Cards that put NONTOKEN creatures onto the battlefield:
    #   reanimation (from graveyard), library cheating, blink
    "nontoken_etb": (
        # Graveyard reanimation
        "lower(oracle_text) LIKE '%return target%creature%battlefield%'"
        " OR lower(oracle_text) LIKE '%creature card from%graveyard%battlefield%'"
        " OR lower(oracle_text) LIKE '%creature card from a graveyard%battlefield%'"
        " OR lower(oracle_text) LIKE '%put target%creature%card%battlefield%'"
        # Library cheating
        " OR lower(oracle_text) LIKE '%search your library for a%creature%battlefield%'"
        " OR lower(oracle_text) LIKE '%look at the top%put%creature%battlefield%'"
        # Blink
        " OR lower(oracle_text) LIKE '%exile target%return%battlefield%'"
    ),

    # Cards that put ANY creatures onto the battlefield (tokens + reanimation + library)
    "creature_etb": (
        # Token creation
        "lower(oracle_text) LIKE '%create a%'"
        " OR lower(oracle_text) LIKE '%create two%'"
        " OR lower(oracle_text) LIKE '%create three%'"
        # Graveyard reanimation
        " OR lower(oracle_text) LIKE '%return target%creature%battlefield%'"
        " OR lower(oracle_text) LIKE '%creature card from%graveyard%battlefield%'"
        " OR lower(oracle_text) LIKE '%creature card from a graveyard%battlefield%'"
        # Library cheating
        " OR lower(oracle_text) LIKE '%search your library for a%creature%battlefield%'"
        " OR lower(oracle_text) LIKE '%look at the top%put%creature%battlefield%'"
        # Blink
        " OR lower(oracle_text) LIKE '%exile target%return%battlefield%'"
    ),

    # Cards that put artifacts onto the battlefield (artifact token makers, cheat-into-play)
    "artifact_etb": (
        "lower(oracle_text) LIKE '%create%treasure%'"
        " OR lower(oracle_text) LIKE '%create%food%'"
        " OR lower(oracle_text) LIKE '%create%clue%'"
        " OR lower(oracle_text) LIKE '%create%gold%'"
        " OR lower(oracle_text) LIKE '%put%artifact%battlefield%'"
        " OR lower(type_line) LIKE '%artifact%'"
    ),

    # Cards that PUT things onto the battlefield (token generators, reanimation, blink)
    "enters_battlefield": (
        "lower(oracle_text) LIKE '%create a%'"
        " OR lower(oracle_text) LIKE '%create two%'"
        " OR lower(oracle_text) LIKE '%create three%'"
        " OR lower(oracle_text) LIKE '%put onto the battlefield%'"
        " OR lower(oracle_text) LIKE '%return target%creature%battlefield%'"
        " OR lower(oracle_text) LIKE '%exile target%return%battlefield%'"
    ),

    # Cards that cause nontoken creatures to die (Aristocrats: sac outlets, kill spells, wipes)
    "nontoken_dies": (
        "lower(oracle_text) LIKE '%sacrifice a creature%'"
        " OR lower(oracle_text) LIKE '%sacrifice another%'"
        " OR lower(oracle_text) LIKE '%destroy target creature%'"
        " OR lower(oracle_text) LIKE '%destroy all creatures%'"
        " OR lower(oracle_text) LIKE '%each creature dies%'"
        " OR lower(oracle_text) LIKE '%creatures your opponents control%'"
    ),

    # Cards that cause any creature to die
    "dies": (
        "lower(oracle_text) LIKE '%sacrifice a creature%'"
        " OR lower(oracle_text) LIKE '%sacrifice another%'"
        " OR lower(oracle_text) LIKE '%destroy target creature%'"
        " OR lower(oracle_text) LIKE '%destroy all%'"
        " OR lower(oracle_text) LIKE '%deals damage%'"
    ),

    # Cards that enable or create attacking creatures (haste, tokens that attack)
    "attacks": (
        "lower(oracle_text) LIKE '% haste%'"
        " OR lower(oracle_text) LIKE '%must attack%'"
        " OR lower(oracle_text) LIKE '%attacks each combat%'"
        " OR lower(oracle_text) LIKE '%attacks each turn%'"
        " OR lower(oracle_text) LIKE '%with haste%'"
    ),

    # Instant and sorcery spells are the natural producers of "whenever you cast" triggers.
    # Also includes storm/cascade/flashback which generate extra casts.
    "spell_cast": (
        "lower(type_line) LIKE '%instant%'"
        " OR lower(type_line) LIKE '%sorcery%'"
        " OR lower(oracle_text) LIKE '%storm%'"
        " OR lower(oracle_text) LIKE '%cascade%'"
        " OR lower(oracle_text) LIKE '%flashback%'"
        " OR lower(oracle_text) LIKE '%cast another%'"
        " OR lower(oracle_text) LIKE '%cast an additional%'"
    ),

    # Cards with beginning-of-phase triggers or that accelerate phase effects
    "phase_begin": (
        "lower(oracle_text) LIKE '%at the beginning of%'"
        " OR lower(oracle_text) LIKE '%during your upkeep%'"
        " OR lower(oracle_text) LIKE '%each upkeep%'"
    ),

    # Cards that put lands into play (fetch effects, ramp spells)
    "landfall": (
        "lower(oracle_text) LIKE '%search your library for a%land%'"
        " OR lower(oracle_text) LIKE '%put a basic land%'"
        " OR lower(oracle_text) LIKE '%put a land%battlefield%'"
        " OR lower(oracle_text) LIKE '%play an additional land%'"
        " OR lower(oracle_text) LIKE '%land card onto the battlefield%'"
    ),

    # Cards that cause discarding (wheels, loot effects, discard outlets)
    "discard": (
        "lower(oracle_text) LIKE '%discard a card%'"
        " OR lower(oracle_text) LIKE '%discard your hand%'"
        " OR lower(oracle_text) LIKE '%each player discards%'"
        " OR lower(oracle_text) LIKE '%target player discards%'"
        " OR lower(oracle_text) LIKE '%discard two%'"
        " OR lower(oracle_text) LIKE '%draw a card, then discard%'"
    ),

    # Cards that specifically create tokens
    "token_creation": (
        "lower(oracle_text) LIKE '%create a%token%'"
        " OR lower(oracle_text) LIKE '%create two%'"
        " OR lower(oracle_text) LIKE '%create three%'"
        " OR lower(oracle_text) LIKE '%create x%'"
        " OR lower(oracle_text) LIKE '%put a%token%onto the battlefield%'"
    ),

    # Cards that add counters (proliferate, +1/+1 counter engines)
    "counter_added": (
        "lower(oracle_text) LIKE '%proliferate%'"
        " OR lower(oracle_text) LIKE '%put a +1/+1 counter%'"
        " OR lower(oracle_text) LIKE '%+1/+1 counter on each%'"
        " OR lower(oracle_text) LIKE '%put a counter on%'"
        " OR lower(oracle_text) LIKE '%double the number of counters%'"
    ),

    # Cards with evasion or power that deal combat damage
    "combat_damage": (
        "lower(oracle_text) LIKE '%can''t be blocked%'"
        " OR lower(oracle_text) LIKE '%double strike%'"
        " OR lower(oracle_text) LIKE '%trample%'"
        " OR lower(oracle_text) LIKE '%menace%'"
        " OR lower(oracle_text) LIKE '%deals combat damage%'"
    ),

    # Sacrifice outlets (cards that let you sacrifice as cost or effect)
    "sacrifice": (
        "lower(oracle_text) LIKE '%sacrifice a creature%'"
        " OR lower(oracle_text) LIKE '%sacrifice another%'"
        " OR lower(oracle_text) LIKE '%sacrifice a permanent%'"
        " OR lower(oracle_text) LIKE '%sacrifice target%'"
        " OR lower(oracle_text) LIKE '%sacrifice:%'"
    ),
}
