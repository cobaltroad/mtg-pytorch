"""Utility-role synergy patterns and producer SQL fragments.

Covers the "support role" patterns that most Commander decks include regardless
of archetype: card draw / advantage engines, targeted and mass removal, tutor
effects, counter magic, instant-speed protection, and combat tricks.

Sub-categories and their trigger event IDs:

Draw / advantage
    spell_draw      — draw or advantage when a spell is cast or a player draws
                      (Rhystic Study, Smothering Tithe, Consecrated Sphinx)
    creature_draw   — draw when a creature enters the battlefield or dies
                      (Toski Bearer of Secrets, Reconnaissance Mission,
                      Grim Haruspex, Welcoming Vampire)
    wheel           — mass draw / loot effects that refill all hands simultaneously
                      (Windfall, Wheel of Fortune, Wheel of Fate, Jace's Archivist)

Removal
    targeted_removal — destroy or exile a single target permanent
                       (Swords to Plowshares, Path to Exile, Krosan Grip)
    burn             — deal damage to a single target
                       (Lightning Bolt, Searing Spear, Fiery Confluence)
    wither           — apply -1/-1 counters or a -X/-X debuff to a single target
                       (Skinrender, Contagion Clasp, Black Sun's Zenith single-target)
    bounce           — return a target permanent to its owner's hand
                       (Unsummon, Boomerang, Into the Roil, Cyclonic Rift)
    sweeper          — destroy, exile, damage, or bounce all (or all of a type)
                       (Wrath of God, Damnation, Blasphemous Act, Evacuation,
                       Cyclonic Rift overload, Toxic Deluge)

Tutor
    tutor_creature  — search library for a creature card
                      (Chord of Calling, Eladamri's Call, Finale of Devastation)
    tutor_artifact  — search library for an artifact card
                      (Fabricate, Tezzeret the Seeker, Whir of Invention)
    tutor_any       — search library for any card (generic tutors)
                      (Demonic Tutor, Vampiric Tutor, Imperial Seal)

Interaction
    counterspell    — counter a spell or ability on the stack
                      (Counterspell, Negate, Swan Song, Fierce Guardianship)
    protection      — grant indestructible, hexproof, or phasing at instant speed
                      (Heroic Intervention, Teferi's Protection, Flawless Maneuver)

Combat tricks
    combat_tricks   — temporarily grant evasion, power/toughness, or combat keywords
                      (Giant Growth, Slip Through Space, Temur Battle Rage)
"""

from __future__ import annotations

# ── Trigger patterns ──────────────────────────────────────────────────────────

TRIGGER_PATTERNS: list[tuple[str, str, str]] = [
    # ── Draw / advantage ──────────────────────────────────────────────────────

    # spell_draw: payoffs that draw cards (or create value) when an opponent
    # casts a spell or when any player draws.  Covers the "Rhystic Study"
    # template ("whenever an opponent casts a spell … draw a card") and the
    # "Smothering Tithe" tax template ("whenever an opponent draws a card").
    # Also matches impulse-draw effects that exile the top of the library and
    # allow you to play it ("exile the top card … you may play it"), treating
    # them as a form of spell-cast card advantage per the impulse-draw
    # discussion in issue #11.
    (
        r"when(ever)?\s+(a player|an opponent).{0,20}(casts? a spell|draws? a card)"
        r"|exile the top \S+ cards? of your library.{0,60}you may (play|cast)",
        "Spell-cast / player-draw advantage trigger",
        "spell_draw",
    ),

    # creature_draw: draw a card whenever a creature enters the battlefield,
    # dies, or deals combat damage.  Examples: Toski Bearer of Secrets,
    # Reconnaissance Mission, Coastal Piracy, Grim Haruspex, Welcoming Vampire.
    # The .{0,20} before "creature" accommodates qualifiers such as "nontoken"
    # or "another nontoken" that appear between the trigger word and "creature".
    (
        r"when(ever)?\s+.{0,20}creature.{0,60}"
        r"(enters?.{0,30}battlefield|dies|deals? combat damage).{0,80}draw (a card|cards?)"
        r"|when(ever)?\s+.{0,20}creature.{0,20}(enters?|dies).{0,40},\s*(you may )?draw (a card|cards?)",
        "Creature ETB / dies / combat-damage draw trigger",
        "creature_draw",
    ),

    # wheel: mass-draw / loot effects that refill all players' hands or let a
    # player draw many cards at once.  Windfall, Wheel of Fortune, Wheel of
    # Fate, Jace's Archivist, Reforge the Soul, etc.
    (
        r"each player (draws|discards.{0,20}then draws)"
        r"|draw (cards? equal to|seven cards|x cards?)"
        r"|each player may draw",
        "Wheel / mass-draw effect",
        "wheel",
    ),

    # ── Removal ───────────────────────────────────────────────────────────────

    # targeted_removal: single-target spells that destroy or exile a permanent.
    # Swords to Plowshares, Path to Exile, Krosan Grip, Generous Gift, etc.
    (
        r"destroy target (creature|permanent|artifact|enchantment|planeswalker|nonland permanent)"
        r"|exile target (creature|permanent|artifact|enchantment|planeswalker|nonland permanent)",
        "Targeted destroy / exile removal",
        "targeted_removal",
    ),

    # burn: single-target direct damage to a creature, player, or planeswalker.
    # Lightning Bolt, Searing Spear, Chaos Warp (damage sub-mode), etc.
    # "any target" is the modern templating for "target creature or player".
    # The X variant uses uppercase X to match actual Magic oracle text convention.
    (
        r"deals? \w+ damage to (target (creature|player|opponent|planeswalker)|any target)"
        r"|deals? X damage to (target|any)",
        "Burn / single-target direct damage",
        "burn",
    ),

    # wither: targeted -1/-1 counter application or temporary -X/-X debuff.
    # Skinrender, Contagion Clasp, Power Conduit, etc.
    (
        r"put(s)? (\w+ )?-1/-1 counters? on (target|a)"
        r"|-\d+/-\d+ until end of turn",
        "Wither / -1/-1 counter removal",
        "wither",
    ),

    # bounce: return a single target permanent to its owner's hand.
    # Unsummon, Boomerang, Into the Roil, Cyclonic Rift (non-overload), etc.
    (
        r"return target (creature|permanent|nonland permanent|artifact|enchantment|planeswalker)"
        r".{0,30}(to its owner's hand|to their owner's hand|to your hand)",
        "Bounce removal",
        "bounce",
    ),

    # sweeper: mass removal affecting all (or all of a type) simultaneously.
    # Wrath of God, Damnation, Blasphemous Act, Cyclonic Rift (overload),
    # Toxic Deluge, In Garruk's Wake, Evacuation, Black Sun's Zenith, etc.
    # The -[\dx]+ patterns cover both numeric (-3/-3) and variable (-X/-X) debuffs.
    (
        r"destroy (all|each) (creatures?|permanents?|nonland permanents?|artifacts?|enchantments?)"
        r"|exile (all|each) (creatures?|permanents?|nonland permanents?|artifacts?|enchantments?)"
        r"|deals? \w+ damage to (all|each) creature"
        r"|(all|each) creatures?.{0,20}(gets?|takes?|receives?).{0,30}-[\dx]+/-[\dx]+"
        r"|put.{0,30}-1/-1 counters? on (all|each) creature"
        r"|return (all|each) (nonland permanents?|permanents?|creatures?)"
        r".{0,30}(to (its|their|your).{0,10}hand|to their owners'? hand)",
        "Sweeper / mass removal",
        "sweeper",
    ),

    # ── Tutors ────────────────────────────────────────────────────────────────

    # tutor_creature: search library for a creature card.
    # Chord of Calling, Eladamri's Call, Finale of Devastation, etc.
    (
        r"search your library for (a |an )?(creature card|legendary creature|creature spell)",
        "Creature tutor",
        "tutor_creature",
    ),

    # tutor_artifact: search library for an artifact card or equipment.
    # Fabricate, Tezzeret the Seeker, Whir of Invention, Reshape, etc.
    (
        r"search your library for (a |an )?(artifact card|equipment card|artifact or enchantment card)",
        "Artifact tutor",
        "tutor_artifact",
    ),

    # tutor_any: generic library search for any card.
    # Demonic Tutor, Vampiric Tutor, Imperial Seal, etc.
    # Intentionally broad — specific-type tutors will also match this pattern.
    (
        r"search your library for (a |an )?card",
        "Generic tutor",
        "tutor_any",
    ),

    # ── Interaction ───────────────────────────────────────────────────────────

    # counterspell: effects that counter a spell or ability on the stack.
    # Counterspell, Negate, Swan Song, Fierce Guardianship, Force of Will, etc.
    # The catch-all `counter target.{0,60}\bspell\b` handles cards like Swan Song
    # that name multiple spell types before the word "spell".
    (
        r"counter target (spell|ability|activated ability|triggered ability"
        r"|instant|sorcery|creature spell|noncreature spell)"
        r"|counter target.{0,60}\bspell\b"
        r"|counter(s)? (that|the) (spell|ability)",
        "Counter target spell / ability",
        "counterspell",
    ),

    # protection: instant-speed effects that grant indestructible, hexproof, or
    # phasing to protect your permanents from removal or board wipes.
    # Heroic Intervention, Teferi's Protection, Flawless Maneuver, etc.
    # Both word orders are matched: "gains hexproof … until end of turn" and
    # "until end of turn … gain hexproof" (Heroic Intervention style).
    (
        r"(gain(s)?|have|has|get(s)?).{0,30}"
        r"(indestructible|hexproof|protection from everything).{0,30}until end of turn"
        r"|until end of turn.{0,80}(gain(s)?|have|has|get(s)?).{0,30}(indestructible|hexproof)"
        r"|\bphase out\b"
        r"|all .{0,50}(gain(s)?|have|has).{0,30}"
        r"(indestructible|hexproof).{0,30}until end of turn",
        "Instant-speed protection",
        "protection",
    ),

    # ── Combat tricks ─────────────────────────────────────────────────────────

    # combat_tricks: instant-speed effects that grant evasion, power/toughness
    # boosts, or relevant combat keywords until end of turn.
    # Giant Growth, Slip Through Space, Temur Battle Rage, Titanic Boon, etc.
    # Matches both "until end of turn" (standard) and "this turn" (some cards)
    # for the unblockable / keyword-grant templates.
    (
        r"(gain(s)?|get(s)?|has|have).{0,50}"
        r"(flying|menace|trample|deathtouch|first strike|double strike|lifelink|vigilance)"
        r".{0,30}until end of turn"
        r"|(get(s)?|gain(s)?).{0,20}\+\d+/\+\d+.{0,20}until end of turn"
        r"|can't be blocked.{0,20}(until end of turn|this turn)",
        "Combat trick / evasion or power grant",
        "combat_tricks",
    ),
]

# ── Producer map ──────────────────────────────────────────────────────────────

PRODUCER_MAP: dict[str, str] = {
    # Instants and sorceries are what opponents cast to trigger Rhystic Study;
    # group-draw effects (Howling Mine, Dictate of Kruphix) make opponents draw
    # and trigger Smothering Tithe; impulse-draw effects (Light Up the Stage,
    # Valakut Awakening) exile the top of the library and provide card advantage
    # comparable to drawing.
    "spell_draw": (
        "lower(type_line) LIKE '%instant%'"
        " OR lower(type_line) LIKE '%sorcery%'"
        " OR lower(oracle_text) LIKE '%each player draws%'"
        " OR lower(oracle_text) LIKE '%players draw a card%'"
        " OR lower(oracle_text) LIKE '%draw two cards%'"
        " OR lower(oracle_text) LIKE '%draw three cards%'"
        # Impulse draw: exile top of library with a "you may play/cast" window
        " OR lower(oracle_text) LIKE '%exile the top%you may%play%'"
        " OR lower(oracle_text) LIKE '%exile the top%you may%cast%'"
    ),

    # Creature generators and death enablers repeatedly trigger creature ETB /
    # dies draw payoffs (Reconnaissance Mission, Grim Haruspex, etc.).
    "creature_draw": (
        "lower(oracle_text) LIKE '%create a%token%'"
        " OR lower(oracle_text) LIKE '%create two%'"
        " OR lower(oracle_text) LIKE '%create three%'"
        " OR lower(oracle_text) LIKE '%return target%creature%battlefield%'"
        " OR lower(oracle_text) LIKE '%creature card from%graveyard%battlefield%'"
        " OR lower(oracle_text) LIKE '%sacrifice a creature%'"
        " OR lower(oracle_text) LIKE '%sacrifice another%'"
        " OR lower(oracle_text) LIKE '%destroy target creature%'"
    ),

    # Cards that benefit from having a full hand or that trigger on each draw
    # are the natural payoffs for wheel effects.
    "wheel": (
        "lower(oracle_text) LIKE '%whenever you draw a card%'"
        " OR lower(oracle_text) LIKE '%for each card drawn%'"
        " OR lower(oracle_text) LIKE '%no maximum hand size%'"
        " OR lower(oracle_text) LIKE '%maximum hand size%'"
        " OR lower(oracle_text) LIKE '%if you have%cards in hand%'"
        " OR lower(oracle_text) LIKE '%seven or more cards in hand%'"
        " OR lower(oracle_text) LIKE '%draw a card, then discard%'"
    ),

    # Evasive and high-value attackers benefit from targeted removal clearing
    # blockers; combo pieces that need specific opponent threats removed are
    # the primary justification for including spot removal.
    "targeted_removal": (
        "lower(oracle_text) LIKE '%can''t be blocked%'"
        " OR lower(oracle_text) LIKE '%double strike%'"
        " OR lower(oracle_text) LIKE '%deals combat damage%'"
        " OR lower(oracle_text) LIKE '%win the game%'"
        " OR lower(type_line) LIKE '%planeswalker%'"
    ),

    # Damage amplifiers and "whenever a source deals damage" payoffs pair with
    # burn spells to compound their effect.
    "burn": (
        "lower(oracle_text) LIKE '%if a source would deal damage%'"
        " OR lower(oracle_text) LIKE '%deals double%damage%'"
        " OR lower(oracle_text) LIKE '%damage is doubled%'"
        " OR lower(oracle_text) LIKE '%whenever a source deals damage%'"
        " OR lower(oracle_text) LIKE '%whenever%deals damage%'"
    ),

    # Cards with the wither / infect keyword, -1/-1 counter engines, and
    # proliferate effects extend the value of wither-removal consumers.
    "wither": (
        "lower(oracle_text) LIKE '%wither%'"
        " OR lower(oracle_text) LIKE '%infect%'"
        " OR lower(oracle_text) LIKE '%-1/-1 counter%'"
        " OR lower(oracle_text) LIKE '%put a -1/-1%'"
        " OR lower(oracle_text) LIKE '%proliferate%'"
    ),

    # ETB-value creatures are the primary targets for bounce: replaying them
    # generates repeated enter-the-battlefield triggers (blink synergy).
    "bounce": (
        "lower(oracle_text) LIKE '%when%enters the battlefield%'"
        " OR lower(oracle_text) LIKE '%exile target%return%battlefield%'"
        " OR lower(oracle_text) LIKE '%blink%'"
        " OR lower(oracle_text) LIKE '%flicker%'"
    ),

    # Strategies that rebuild quickly after a board wipe (graveyard recursion),
    # survive it (indestructible), or re-flood the board in one action (token
    # generators) are the natural companion pieces to sweepers.
    "sweeper": (
        # Graveyard-based recovery
        "lower(oracle_text) LIKE '%return%from%graveyard%'"
        " OR lower(oracle_text) LIKE '%creature card from%graveyard%'"
        # Indestructible permanents survive wipes
        " OR lower(oracle_text) LIKE '%indestructible%'"
        # Token generators rebuild boards quickly
        " OR lower(oracle_text) LIKE '%create a%token%'"
        " OR lower(oracle_text) LIKE '%create two%'"
        " OR lower(oracle_text) LIKE '%create three%'"
    ),

    # Legendary creatures and combo-piece creatures are premium creature tutor
    # targets; ETB-value creatures are worth tutoring into play as well.
    "tutor_creature": (
        "(lower(type_line) LIKE '%creature%' AND lower(type_line) LIKE '%legendary%')"
        " OR (lower(type_line) LIKE '%creature%'"
        "     AND lower(oracle_text) LIKE '%win the game%')"
        " OR (lower(type_line) LIKE '%creature%'"
        "     AND lower(oracle_text) LIKE '%each opponent loses%')"
        " OR (lower(type_line) LIKE '%creature%'"
        "     AND lower(oracle_text) LIKE '%when%enters the battlefield%')"
    ),

    # Equipment, legendary artifacts, mana-producing artifacts, and combo
    # artifacts are the primary artifact tutor targets.
    "tutor_artifact": (
        "lower(type_line) LIKE '%equipment%'"
        " OR (lower(type_line) LIKE '%artifact%' AND lower(type_line) LIKE '%legendary%')"
        " OR (lower(type_line) LIKE '%artifact%'"
        "     AND lower(oracle_text) LIKE '%add%mana%')"
        " OR (lower(type_line) LIKE '%artifact%'"
        "     AND lower(oracle_text) LIKE '%win the game%')"
    ),

    # The most powerful cards worth spending a generic tutor slot on: legendary
    # permanents, alternate win conditions, and high-value finishers.
    "tutor_any": (
        "lower(type_line) LIKE '%legendary%'"
        " OR lower(oracle_text) LIKE '%win the game%'"
        " OR lower(oracle_text) LIKE '%each opponent loses%'"
        " OR lower(oracle_text) LIKE '%you win the game%'"
    ),

    # Instants and sorceries are what counterspells protect against (or protect
    # in hand); cards that explicitly state they "can't be countered" are also
    # relevant as they co-exist with counter-magic in control builds.
    "counterspell": (
        "lower(type_line) LIKE '%instant%'"
        " OR lower(type_line) LIKE '%sorcery%'"
        " OR lower(oracle_text) LIKE '%can''t be countered%'"
    ),

    # High-value creatures, planeswalkers, and legendary permanents are the
    # natural targets for instant-speed protection effects.
    "protection": (
        "lower(type_line) LIKE '%creature%'"
        " OR lower(type_line) LIKE '%planeswalker%'"
        " OR lower(type_line) LIKE '%legendary%'"
        " OR lower(oracle_text) LIKE '%when%dies%'"
    ),

    # Creatures that deal combat damage or that have combat-triggered abilities
    # benefit most from evasion grants and power/toughness boosts.
    "combat_tricks": (
        "lower(oracle_text) LIKE '%deals combat damage%'"
        " OR lower(oracle_text) LIKE '%whenever%attacks%'"
        " OR (lower(type_line) LIKE '%creature%'"
        "     AND lower(oracle_text) LIKE '%whenever%damage%')"
    ),
}
