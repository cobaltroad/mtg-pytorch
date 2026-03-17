"""
Card role detection — Issue #31.

Derives functional role tags from oracle text / type line and writes them to
`card_abilities` (ability_type='role').  Tags are intentionally machine-readable
coefficients, not prose.  They feed into synergy_edges as role_demand score type,
closing the training feedback loop.

Roles
-----
ramp            — mana acceleration / fixing
draw            — card advantage / card selection
removal         — spot removal, sweepers, bounce
tutor           — library search
protection      — hexproof / indestructible / shroud / regenerate / recursion
win_condition   — infect, alternate win cons, storm, combat finishers
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

    # win_condition — alternate or dominant win paths
    ("win_condition", re.compile(r"\binfect\b", re.I), "infect"),
    ("win_condition", re.compile(r"\btoxic\b", re.I), "toxic"),
    ("win_condition", re.compile(r"you win the game", re.I), "alt_win"),
    ("win_condition", re.compile(r"\bstorm\b", re.I), "storm"),
    ("win_condition", re.compile(r"(deal|deals) \d+ or more (combat )?damage to (a player|each opponent)", re.I), "combat_finisher"),
    ("win_condition", re.compile(r"\bdoublestrike\b|\bdouble strike\b", re.I), "double_strike"),
    ("win_condition", re.compile(r"\btrample\b", re.I), "trample"),
]


_LAND_RAMP_SKIP = {"mana_rock", "mana_ability"}  # patterns that are noise on land cards

def detect_roles(oracle_text: str, type_line: str = "", keywords: list[str] | None = None) -> list[tuple[str, str]]:
    """Return list of (role, effect_class) for a card based on oracle text.

    Runs all _ROLE_PATTERNS against the combined oracle + keyword text.
    Multiple roles are possible; deduplication is done by (role, effect_class).
    Land cards are excluded from mana_rock / mana_ability ramp tags since those
    patterns match every basic land — ramp means accelerating *beyond* your land drops.
    """
    keywords = keywords or []
    is_land = "land" in type_line.lower()
    combined = f"{oracle_text}\n{' '.join(keywords)}".lower()

    seen: set[tuple[str, str]] = set()
    results: list[tuple[str, str]] = []
    for role, pattern, effect_class in _ROLE_PATTERNS:
        # Skip mana-ability patterns for plain land cards
        if is_land and role == "ramp" and effect_class in _LAND_RAMP_SKIP:
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
# They drive role_demand weighting: e.g. an aristocrats commander values sacrifice
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

    # Voltron / equipment
    ("voltron",         re.compile(r"equipped creature gets?\b|\bequip \{", re.I)),
    ("voltron",         re.compile(r"whenever .{0,30}becomes? enchanted", re.I)),

    # Landfall
    ("landfall",        re.compile(r"\blandfall\b|whenever (a |one or more )?land.{0,20}enters", re.I)),

    # Lifegain payoff
    ("lifegain",        re.compile(r"whenever you (gain|gained) life", re.I)),
]

# How each archetype shifts role demand weights (multiplier on base frequency)
ARCHETYPE_ROLE_WEIGHTS: dict[str, dict[str, float]] = {
    "go_wide":       {"win_condition": 1.5, "protection": 1.3},
    "aristocrats":   {"win_condition": 1.5, "removal": 0.7},
    "counters":      {"draw": 1.2, "protection": 1.3},
    "graveyard":     {"tutor": 1.3, "protection": 0.8},
    "spellslinger":  {"draw": 1.5, "tutor": 1.3, "removal": 0.8},
    "voltron":       {"protection": 2.0, "win_condition": 1.5},
    "landfall":      {"ramp": 1.4},
    "lifegain":      {"win_condition": 1.3},
}
# Tribal commanders implicitly value all tribal sub-roles equally
for _t in [k for k in _ARCHETYPE_PATTERNS if k[0].startswith("tribal_")]:
    ARCHETYPE_ROLE_WEIGHTS[_t[0]] = {"win_condition": 1.2}


def detect_archetypes(oracle_text: str, type_line: str = "") -> list[str]:
    """Return list of archetype tags for a commander based on its oracle text."""
    combined = f"{type_line}\n{oracle_text}".lower()
    seen: set[str] = set()
    results: list[str] = []
    for archetype, pattern in _ARCHETYPE_PATTERNS:
        if archetype not in seen and pattern.search(combined):
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


async def write_role_demand_edges(
    db: AsyncSession,
    commander_id: str,          # UUID as string (cards.id)
    role_counts: dict[str, int],
    total_deck_cards: int,
    archetypes: list[str] | None = None,
) -> None:
    """Write role_demand synergy edges from commander → cards of each role.

    Base score = count_of_role / total_deck_cards   (0..1 normalised frequency)

    Archetype multipliers (ARCHETYPE_ROLE_WEIGHTS) adjust scores so that, e.g.,
    a Voltron commander's protection cards score 2× their raw frequency.
    Existing edges take the GREATEST score so repeated imports only improve signal.
    """
    archetypes = archetypes or []

    # Aggregate multipliers from all detected archetypes
    multipliers: dict[str, float] = {}
    for arch in archetypes:
        for role, mult in ARCHETYPE_ROLE_WEIGHTS.get(arch, {}).items():
            multipliers[role] = max(multipliers.get(role, 1.0), mult)

    for role, count in role_counts.items():
        base_score  = count / max(total_deck_cards, 1)
        role_score  = min(base_score * multipliers.get(role, 1.0), 1.0)

        await db.execute(text("""
            INSERT INTO synergy_edges (card_a, card_b, score_type, score)
            SELECT DISTINCT ON (ca.card_id)
                CAST(:commander_id AS uuid),
                ca.card_id,
                'role_demand',
                CAST(:score AS float)
            FROM card_abilities ca
            WHERE ca.ability_type = 'role'
              AND ca.ability_name = :role
              AND ca.card_id != CAST(:commander_id AS uuid)
            ON CONFLICT (card_a, card_b, score_type)
                DO UPDATE SET score = GREATEST(synergy_edges.score, EXCLUDED.score)
        """), {
            "commander_id": commander_id,
            "role":         role,
            "score":        role_score,
        })

    await db.commit()
