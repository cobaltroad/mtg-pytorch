"""
Card role detection — Issue #31.

Derives functional role tags from oracle text / type line and writes them to
`card_abilities` (ability_type='role').  Tags are intentionally machine-readable
coefficients, not prose.  They are used by the deck builder to classify cards
and by the deck browser to annotate card lists.

Roles
-----
ramp            — mana acceleration / fixing
draw            — card advantage / card selection
removal         — spot removal, sweepers, bounce
tutor           — library search
protection      — hexproof / indestructible / shroud / regenerate / recursion
win_condition   — infect, alternate win cons, storm, combat finishers
token           — creature/token generation
aura_equipment  — the card is an Equipment or creature-targeting Aura
combat_trick    — evasion / unblockable / haste grants that help a creature connect
discard_trigger — payoffs that fire when a card is discarded (Bone Miser, Waste Not)
etb_trigger     — payoffs that fire when creatures enter the battlefield (Impact Tremors)
wide_payoff     — scales with number of creatures / tokens on board (Bravado, Coat of Arms)
sac_outlet      — lets you sacrifice creatures for value (Goblin Bombardment, Altar)
"""

from __future__ import annotations

import re
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

# ── Detection patterns ────────────────────────────────────────────────────────
# Each entry: (role, pattern, effect_class)
_ROLE_PATTERNS: list[tuple[str, re.Pattern[str], str]] = [
    # ramp — mana sources & land tutors
    ("ramp", re.compile(r"\{t\}\s*:\s*add", re.I), "mana_rock"),
    ("ramp", re.compile(r"add \{[cwubrg]\}", re.I), "mana_ability"),
    ("ramp", re.compile(r"add (one|two|three|x) mana", re.I), "mana_ability"),
    ("ramp", re.compile(r"create (a |an |\d+ |x )?(gold|treasure|food|clue) token", re.I), "treasure"),
    ("ramp", re.compile(r"search your library for (a |up to \w+ )?(basic |snow )?(land|plains|island|swamp|mountain|forest)", re.I), "land_tutor"),
    ("ramp", re.compile(r"put (that card |a basic land card |it )?onto the battlefield (tapped )?from your hand", re.I), "land_ramp"),
    ("ramp", re.compile(r"you may play (an? )?(additional )?land", re.I), "land_ramp"),

    # draw — card advantage
    ("draw", re.compile(r"draw (a card|x cards|\w+ cards)", re.I), "card_draw"),
    ("draw", re.compile(r"draw cards equal", re.I), "card_draw"),
    ("draw", re.compile(r"look at the top \w+ cards? of your library", re.I), "impulse"),
    ("draw", re.compile(r"each player draws", re.I), "wheel"),
    ("draw", re.compile(r"(discard|put).{0,30}then draw", re.I), "loot"),
    ("draw", re.compile(r"you may (draw|loot|look)", re.I), "card_draw"),

    # removal — spot and sweepers
    ("removal", re.compile(r"destroy target (creature|permanent|artifact|enchantment|planeswalker)", re.I), "spot_removal"),
    ("removal", re.compile(r"exile target (creature|permanent|artifact|enchantment|planeswalker|card)", re.I), "exile_removal"),
    ("removal", re.compile(r"deals? \w+ damage to (any target|target creature|each creature|each opponent)", re.I), "damage_removal"),
    ("removal", re.compile(r"return target .{0,40} to (its owner's hand|hand)", re.I), "bounce"),
    ("removal", re.compile(r"destroy all (creatures|nonland permanents|artifacts|enchantments)", re.I), "sweeper"),
    ("removal", re.compile(r"exile all (creatures|nonland permanents|artifacts)", re.I), "sweeper"),
    ("removal", re.compile(r"\-\w+/-\w+", re.I), "debuff"),  # -X/-X pump effects
    ("removal", re.compile(r"counter target (spell|creature spell|instant|sorcery|ability)", re.I), "counter"),

    # tutor — library search (non-land)
    ("tutor", re.compile(r"search your library for (a |an )?(?!.*land)(creature|artifact|instant|sorcery|enchantment|planeswalker|card)", re.I), "tutor"),
    ("tutor", re.compile(r"search your library for (up to|any number of)", re.I), "tutor"),

    # protection — survivability
    ("protection", re.compile(r"\bhexproof\b", re.I), "hexproof"),
    ("protection", re.compile(r"\bindestructible\b", re.I), "indestructible"),
    ("protection", re.compile(r"\bshroud\b", re.I), "shroud"),
    ("protection", re.compile(r"\bregenerate\b", re.I), "regenerate"),
    ("protection", re.compile(r"return .{0,40} from (your |a )graveyard to .{0,20}(hand|battlefield)", re.I), "recursion"),
    ("protection", re.compile(r"(prevents? all damage|prevent that damage)", re.I), "damage_prevention"),

    # aura_equipment — the card IS an equipment or an aura targeting creatures;
    # these are the primary deckbuilding payload for voltron commanders.
    ("aura_equipment",  re.compile(r"\bequip \{", re.I), "equipment"),
    ("aura_equipment",  re.compile(r"\benchant (creature|permanent)", re.I), "aura"),

    # combat_trick — *grants* evasion to another creature; what you want when
    # your commander needs to connect to trigger its ability.  Patterns require
    # a granting context so cards that merely possess the keyword don't match.
    ("combat_trick", re.compile(
        r"(equipped|enchanted|target|another) creature .{0,60}"
        r"(flying|menace|trample|intimidate|skulk|shadow|fear|double strike)",
        re.I), "evasion_grant"),
    ("combat_trick", re.compile(
        r"(creatures you control|it) (have|has|get|gets|gain|gains) .{0,40}"
        r"(flying|menace|trample|intimidate|skulk|shadow|fear|double strike)",
        re.I), "evasion_grant"),
    ("combat_trick", re.compile(
        r"gains? (flying|menace|trample|intimidate|skulk|shadow|fear|double strike)",
        re.I), "evasion_grant"),
    ("combat_trick", re.compile(
        r"(equipped|enchanted|target) creature .{0,30}can't be blocked",
        re.I), "unblockable_grant"),
    ("combat_trick", re.compile(
        r"(equipped|enchanted) creature has protection from",
        re.I), "protection_grant"),
    ("combat_trick", re.compile(
        r"(creatures you control|it|they) (have|has|get|gets|gain|gains) .{0,20}haste",
        re.I), "haste_grant"),

    # etb_trigger — fires when creatures enter the battlefield under your control
    ("etb_trigger",  re.compile(r"whenever (a |one or more )?creature(s)? (enters|enter) the battlefield (under your control|you control)", re.I), "etb_payoff"),
    ("etb_trigger",  re.compile(r"whenever (a |one or more )?creature(s)? you control enters", re.I), "etb_payoff"),

    # wide_payoff — value scales with creature / token count
    ("wide_payoff",  re.compile(r"for each creature (you control|on the battlefield)", re.I), "count_matters"),
    ("wide_payoff",  re.compile(r"equal to (the number of|x, where x is).{0,30}creature", re.I), "count_matters"),
    ("wide_payoff",  re.compile(r"gets? \+\d+/\+\d+ for each", re.I), "anthem_scale"),

    # sac_outlet — lets you sacrifice creatures as a cost or activated ability
    ("sac_outlet",   re.compile(r"sacrifice (a |an )?(creature|token|goblin|elf|zombie|human)\s*[,:]", re.I), "sac_cost"),
    ("sac_outlet",   re.compile(r"\{[^}]*\},? sacrifice (a|an)? (creature|token)", re.I), "sac_cost"),

    # discard_trigger — payoffs that fire when you (or a player) discards
    ("discard_trigger", re.compile(r"whenever you discard", re.I), "discard_payoff"),
    ("discard_trigger", re.compile(r"whenever (a card is discarded|a player discards)", re.I), "discard_payoff"),

    # token — creature/token generation
    ("token", re.compile(r"create (a |an |\d+ |x ).{0,40}creature token", re.I), "token_gen"),
    ("token", re.compile(r"create (a |an |\d+ |x ).{0,20}token", re.I), "token_gen"),
    ("token", re.compile(r"put (a |an |\d+ ).{0,30}token(s)? onto the battlefield", re.I), "token_gen"),

    # win_condition — alternate or dominant win paths
    ("win_condition", re.compile(r"\binfect\b", re.I), "infect"),
    ("win_condition", re.compile(r"\btoxic\b", re.I), "toxic"),
    ("win_condition", re.compile(r"you win the game", re.I), "alt_win"),
    ("win_condition", re.compile(r"\bstorm\b", re.I), "storm"),
    ("win_condition", re.compile(r"(deal|deals) \d+ or more (combat )?damage to (a player|each opponent)", re.I), "combat_finisher"),
    ("win_condition", re.compile(r"\bdoublestrike\b|\bdouble strike\b", re.I), "double_strike"),
    ("win_condition", re.compile(r"\btrample\b", re.I), "trample"),
]


def detect_roles(oracle_text: str, type_line: str = "", keywords: list[str] | None = None) -> list[tuple[str, str]]:
    """Return list of (role, effect_class) for a card based on oracle text.

    Runs all _ROLE_PATTERNS against the combined oracle + keyword text.
    Multiple roles are possible; deduplication is done by (role, effect_class).
    Land cards are never tagged as ramp — lands are land drops, not acceleration.
    """
    keywords = keywords or []
    is_land = "land" in type_line.lower()
    combined = f"{oracle_text}\n{' '.join(keywords)}".lower()

    seen: set[tuple[str, str]] = set()
    results: list[tuple[str, str]] = []
    for role, pattern, effect_class in _ROLE_PATTERNS:
        if is_land and role == "ramp":
            continue
        key = (role, effect_class)
        if key in seen:
            continue
        if pattern.search(combined):
            seen.add(key)
            results.append((role, effect_class))

    return results


async def tag_card_roles(
    db: AsyncSession,
    card_id: str,          # UUID as string
    oracle_text: str,
    type_line: str = "",
    keywords: list[str] | None = None,
) -> list[tuple[str, str]]:
    """Detect roles and upsert them into card_abilities.  Returns the detected roles."""
    roles = detect_roles(oracle_text, type_line, keywords)

    for role, effect_class in roles:
        await db.execute(text("""
            INSERT INTO card_abilities
                (card_id, ability_type, ability_name, trigger_event, effect_class, raw_text)
            VALUES
                (CAST(:card_id AS uuid), 'role', :role, NULL, :effect_class,
                 LEFT(:oracle_text, 500))
            ON CONFLICT (card_id, ability_type, ability_name, effect_class) DO NOTHING
        """), {
            "card_id":    card_id,
            "role":       role,
            "effect_class": effect_class,
            "oracle_text": oracle_text or "",
        })

    await db.commit()
    return roles


async def get_card_roles(db: AsyncSession, card_id: str) -> list[dict]:
    """Return existing role tags for a card from card_abilities."""
    rows = await db.execute(text("""
        SELECT ability_name AS role, effect_class
        FROM card_abilities
        WHERE card_id = CAST(:card_id AS uuid)
          AND ability_type = 'role'
    """), {"card_id": card_id})
    return [{"role": r[0], "effect_class": r[1]} for r in rows]


# ── Commander archetype detection ─────────────────────────────────────────────
# Archetypes capture the commander's *deckbuilding intent*, not just card roles.
# These are stored in card_abilities as ability_type='archetype' on the commander.
# They inform deck-builder role weighting: e.g. an aristocrats commander values sacrifice
# and death-trigger cards more than a generic midrange commander.

_ARCHETYPE_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    # Tribal — commander shares a creature type it cares about
    ("tribal_elves",    re.compile(r"\belf\b|\belves\b", re.I)),
    ("tribal_goblins",  re.compile(r"\bgoblin\b", re.I)),
    ("tribal_zombies",  re.compile(r"\bzombie\b", re.I)),
    ("tribal_vampires", re.compile(r"\bvampire\b", re.I)),
    ("tribal_dragons",  re.compile(r"\bdragon\b", re.I)),
    ("tribal_humans",   re.compile(r"\bhuman\b", re.I)),
    ("tribal_knights",  re.compile(r"\bknight\b", re.I)),
    ("tribal_merfolk",  re.compile(r"\bmerfolk\b", re.I)),
    ("tribal_slivers",  re.compile(r"\bsliver\b", re.I)),
    ("tribal_spirits",  re.compile(r"\bspirit\b", re.I)),
    ("tribal_ninjas",   re.compile(r"\bninja\b|\bninjas\b", re.I)),

    # Go-wide / token strategy
    ("go_wide",         re.compile(r"create (a|an|x|\d+) .{0,20}(token|creature) token", re.I)),
    ("go_wide",         re.compile(r"each creature (you control|gets|has)", re.I)),

    # Aristocrats / sacrifice loops
    ("aristocrats",     re.compile(r"whenever (a|another) (creature|nontoken creature).{0,40}dies", re.I)),
    ("aristocrats",     re.compile(r"whenever you sacrifice", re.I)),

    # +1/+1 counters
    ("counters",        re.compile(r"\+1/\+1 counter", re.I)),
    ("counters",        re.compile(r"\bproliferate\b", re.I)),

    # Graveyard / reanimator
    ("graveyard",       re.compile(r"return .{0,40} from (your |a |the )?graveyard to (the )?battlefield", re.I)),
    ("graveyard",       re.compile(r"from (your |a )?graveyard .{0,30}cast", re.I)),

    # Spellslinger / magecraft
    ("spellslinger",    re.compile(r"whenever you cast (an instant|a sorcery|a noncreature spell)", re.I)),
    ("spellslinger",    re.compile(r"\bmagecraft\b|\bstorm\b", re.I)),

    # Voltron / equipment — catches both cards that are equipment/auras and
    # commanders whose payoff references being equipped/enchanted themselves.
    ("voltron",         re.compile(r"equipped creature gets?\b|\bequip \{", re.I)),
    ("voltron",         re.compile(r"whenever .{0,30}becomes? enchanted", re.I)),
    ("voltron",         re.compile(r"(aura|equipment).{0,60}attached to (it|him|her)", re.I)),
    ("voltron",         re.compile(r"for each (aura|equipment)", re.I)),
    ("voltron",         re.compile(r"whenever .{0,40}attacks,.{0,60}(aura|equipment|equipped|enchanted)", re.I)),

    # Landfall
    ("landfall",        re.compile(r"\blandfall\b|whenever (a |one or more )?land.{0,20}enters", re.I)),

    # Lifegain payoff
    ("lifegain",        re.compile(r"whenever you (gain|gained) life", re.I)),

    # Elfball / mana-ability matters — commander rewards mana dorks producing mana.
    # "mana ability" is the MTG rules term for any activated ability that produces
    # mana without going on the stack (Rule 605), so this matches precisely.
    ("elfball",         re.compile(r"\bmana ability\b", re.I)),

    # Combat damage matters — commander rewards connecting with players
    # Covers self-rewarding commanders (Locke Cole draws/loots on hit) and
    # team-amplifying commanders (Lightning grants the effect to your whole board).
    ("combat_damage",   re.compile(r"whenever .{0,60}deals combat damage to a player", re.I)),

    # Discard outlet — commander gives *you* a way to discard, fueling
    # madness, graveyard strategies, or payoffs like Bone Miser / Waste Not.
    # Covers: loot effects ("draw a card, then discard"), optional discard
    # ("you may discard"), and discard-as-cost activated abilities.
    ("discard_outlet",  re.compile(r"then discard (a|\d+|x) cards?", re.I)),
    ("discard_outlet",  re.compile(r"you may discard", re.I)),
    ("discard_outlet",  re.compile(r"discard (a|\d+|x) cards?:", re.I)),

    # Extra damage — commander multiplies or redirects combat damage dealt
    # (e.g. Lightning, Army of One: "deals that much damage again").
    ("extra_damage",    re.compile(r"deals? .{0,40}(that much|additional \d+|double (that|the)) damage", re.I)),
]

# How each archetype shifts role demand weights (multiplier on base frequency)
ARCHETYPE_ROLE_WEIGHTS: dict[str, dict[str, float]] = {
    "go_wide":       {"etb_trigger": 2.0, "wide_payoff": 1.8, "token": 1.5, "combat_trick": 1.3, "sac_outlet": 1.3},
    "aristocrats":   {"win_condition": 1.5, "removal": 0.7},
    "counters":      {"draw": 1.2, "protection": 1.3},
    "graveyard":     {"tutor": 1.3, "protection": 0.8},
    "spellslinger":  {"draw": 1.5, "tutor": 1.3, "removal": 0.8},
    "voltron":       {"aura_equipment": 2.0, "combat_trick": 1.5, "protection": 1.3},
    "landfall":      {"ramp": 1.4},
    "lifegain":      {"win_condition": 1.3},
    # Combat-damage commanders need the commander itself to connect — evasion
    # and unblockable effects are the primary demand, draw rewards the hits,
    # protection keeps the commander alive between attacks.
    "elfball":       {"ramp": 2.0, "draw": 1.3},
    "combat_damage": {"combat_trick": 2.0, "draw": 1.3, "protection": 1.2},
    # Discard-outlet commanders want payoffs that fire on discard (Bone Miser,
    # Waste Not) and draw/loot to keep the engine churning.
    "discard_outlet": {"discard_trigger": 2.0, "draw": 1.3},
    # Extra-damage commanders multiply each hit — getting through blockers is
    # even more critical, so combat_trick demand is highest of all archetypes.
    "extra_damage":  {"combat_trick": 2.2, "protection": 1.3},
}
# Tribal commanders implicitly want wide-board payoffs and token generators
for _t in [k for k in _ARCHETYPE_PATTERNS if k[0].startswith("tribal_")]:
    ARCHETYPE_ROLE_WEIGHTS[_t[0]] = {"win_condition": 1.2, "wide_payoff": 1.4, "token": 1.3}
# Ninja commanders specifically need evasion to enable ninjutsu and connect
ARCHETYPE_ROLE_WEIGHTS["tribal_ninjas"] = {"win_condition": 1.5, "draw": 1.3}


def detect_archetypes(oracle_text: str, type_line: str = "") -> list[str]:
    """Return list of archetype tags for a commander based on its oracle text.

    Tribal patterns are intentionally matched against oracle_text only — a
    commander's own type line tells us what *it* is, not what it cares about.
    Non-tribal patterns use the combined text so keyword grants in the type
    line (e.g. "Legendary Creature — Sliver") still surface if needed.
    """
    oracle_lower   = oracle_text.lower()
    combined_lower = f"{type_line}\n{oracle_text}".lower()

    seen: set[str] = set()
    results: list[str] = []
    for archetype, pattern in _ARCHETYPE_PATTERNS:
        if archetype in seen:
            continue
        text = oracle_lower if archetype.startswith("tribal_") else combined_lower
        if pattern.search(text):
            seen.add(archetype)
            results.append(archetype)
    return results


async def tag_commander_archetypes(
    db: AsyncSession,
    card_id: str,
    oracle_text: str,
    type_line: str = "",
) -> list[str]:
    """Detect and upsert archetype tags for a commander into card_abilities."""
    archetypes = detect_archetypes(oracle_text, type_line)

    for archetype in archetypes:
        await db.execute(text("""
            INSERT INTO card_abilities
                (card_id, ability_type, ability_name, trigger_event, effect_class, raw_text)
            VALUES
                (CAST(:card_id AS uuid), 'archetype', :archetype, NULL, 'commander_goal',
                 LEFT(:oracle_text, 500))
            ON CONFLICT (card_id, ability_type, ability_name, effect_class) DO NOTHING
        """), {
            "card_id":    card_id,
            "archetype":  archetype,
            "oracle_text": oracle_text or "",
        })

    await db.commit()
    return archetypes

