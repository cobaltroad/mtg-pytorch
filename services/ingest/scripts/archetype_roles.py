"""Role patterns for deck-import archetype detection.

Formerly synergy/roles.py — retired from the card_abilities tagging pipeline
by the mechanics refactor, but scripts/import_utils.detect_archetype still
uses these regexes to label imported human decklists (archetype,
win_conditions, role_counts metadata).  Scoped to scripts/ on purpose:
nothing in the synergy pipeline may import this (#153).

Original module docstring follows.


Each card can have one or more functional roles.  Roles are stored in
``card_abilities`` with ``ability_type = 'role'`` and ``ability_name`` set to
the role string below.

Roles and their detection heuristics
--------------------------------------

ramp
    Anything that accelerates mana: mana rocks, mana dorks, land-to-battlefield
    tutors, "play an additional land" effects.  ``{T}: Add`` in oracle text is
    the primary signal; broad ``add … mana`` patterns catch variable-mana
    producers like Selvala or Cabal Coffers.

draw_one
    A card or ability that draws one or more cards as a singular, non-repeating
    effect.  Includes instants and sorceries that draw, loot (draw-then-discard),
    impulse draw (exile top of library, may cast), cantrips, life-pay draw
    (Necropotence style), and wheel/mass-draw effects that refill all hands
    simultaneously (Windfall, Wheel of Fortune).  These are one-shot effects
    cast from hand, not engines that keep working across multiple turns.

repeatable_draw
    A static, permanent-based source of card advantage that generates ongoing
    draw (or impulse-draw) across multiple turns.  The card stays on the
    battlefield and keeps triggering or can be activated repeatedly.

    Specifically matched:

    * ``\bwhenever\b`` triggered draws — condition fires repeatedly as long as
      the permanent is in play (Rhystic Study, Mystic Remora, Beast Whisperer,
      Reconnaissance Mission, Consecrated Sphinx, Toski).
    * ``at the beginning of`` step-triggered draws — once per turn (Phyrexian
      Arena, Sylvan Library, Howling Mine, Dictate of Kruphix).
    * Activated-ability draws — explicitly cost-driven but repeatable over the
      course of the game (War Room, Sensei's Divining Top, The Immortal Sun).
    * Repeatable impulse engines — at beginning of turn, exile top and may play
      (Precognition Field, Future Sight).

    One-time ETB draw ("When X enters the battlefield, draw a card") and all
    instant / sorcery draw effects belong in ``draw_one``, not here.

removal
    Single-target interaction: destroy, exile, direct-damage, or bounce
    targeting one creature / permanent / planeswalker.

sweeper
    Mass removal: destroy/exile all creatures, deal damage to all creatures,
    -X/-X all creatures, or bounce all nonland permanents.  Includes Overload
    cards (Cyclonic Rift) and variable-cost sweepers (Toxic Deluge).

tutor
    Search your library for a card (any type).  Creature tutors, artifact
    tutors, and generic tutors all receive this tag.

protection
    Static or activated abilities that make permanents hard to remove:
    hexproof, indestructible, shroud, regenerate, phase-out.  Also covers
    instant-speed protection spells (Heroic Intervention) that grant these
    keywords until end of turn.

win_condition
    Cards that can win the game on their own: infect/toxic (10 poison counters
    win), explicit "you win the game" text, and life-drain win conditions.
    Combo-enabler cards that are frequently cited in infinite-loop lines are
    not matched here — win_condition is reserved for cards that directly state
    the win mechanism.

anthem
    Permanent effects that give all (or most) of your creatures +N/+N.
    Covers Dictate of Heliod, Glorious Anthem, Coat of Arms (conditional),
    and similar static boosts.

token_generator
    Cards that create creature or artifact tokens.  The ``create … token``
    oracle text template is the universal signal.

recursion
    Graveyard recursion: return a card from the graveyard to hand or
    battlefield, or put a creature card from a graveyard onto the battlefield.
    Covers both instant ETB recursion (Reanimate) and triggered recursion
    (Sheoldred).

interaction
    Stack-based interaction: hard and conditional counterspells, and redirect
    effects that change targets of spells on the stack.

combat_trick
    Instant-speed pump (+X/+X) or combat-keyword grants (trample, deathtouch,
    first strike, double strike, lifelink, haste) until end of turn.
    Evasion grants (flying, menace, unblockable) are also included.

aura_equipment
    The card IS an Equipment (has an equip cost) or a creature/permanent-targeting
    Aura (``enchant creature`` / ``enchant permanent``).  Voltron commanders
    demand these at high weight.

etb_trigger
    Payoff that fires whenever one or more creatures enter the battlefield under
    your control.  Covers damage pingers (Impact Tremors, Purphoros), counter
    doublers (Cathars' Crusade), and life-gain engines (Essence Warden).

wide_payoff
    Non-pump effect that scales with the number of creatures or tokens on the
    battlefield.  Distinct from ``anthem`` (which covers static +N/+N boosts):
    wide_payoff captures damage-based count-matters effects and draw engines
    that scale with board size.

sac_outlet
    Lets you sacrifice one or more creatures as an activated-ability cost or as
    part of an effect (Goblin Bombardment, Altar of Dementia, Viscera Seer).
    Aristocrats and go-wide commanders value these to convert excess tokens into
    damage, mill, or scry.

discard_trigger
    Payoff that fires when you (or a player) discards a card: Bone Miser,
    Waste Not, Surly Badgersaur.  Discard-outlet commanders demand these.

mana_land  (LAND_ROLE_PATTERNS only)
    A land card whose oracle text contains ``{T}: Add`` — i.e. it taps to
    produce mana.  Applies only when the card's type_line contains "Land".

utility_land  (LAND_ROLE_PATTERNS only)
    A land card that tutors another land or has a non-mana activated ability.
    Fetchlands and other search-and-sacrifice lands receive this tag.
    Applies only when the card's type_line contains "Land".
"""

from __future__ import annotations

# ── Oracle-text role patterns ─────────────────────────────────────────────────
# Each entry: (oracle_regex, role_name, effect_class)
#
# effect_class encodes a structured decomposition of what the card does:
#   removal  → "{mechanism}:{target_scope}"
#              e.g. "exile:creature", "destroy:artifact_enchantment"
#   sweeper  → "{mechanism}:all_{scope}"
#              e.g. "destroy:all_creatures", "bounce:all_permanents"
#   ramp     → mechanism/source type, e.g. "tap:mana", "land:battlefield"
#   draw_*   → delivery mechanism, e.g. "spell:draw", "triggered:draw"
#   tutor    → target type, e.g. "any_card", "creature", "artifact"
#   other    → descriptive keyword matching the keyword that fired
#
# Regexes are matched with re.IGNORECASE; re.DOTALL is NOT used.
# Use [\s\S] or (?s) only where cross-line matching is explicitly needed.
#
# Removal notes:
#   - Compound "or" phrasings (e.g. "artifact or enchantment") must come
#     BEFORE their constituent single-type patterns.
#   - Single-type removal patterns use (?! or ) so they do not accidentally
#     match the first type in a compound phrase.
#   - (?! you control) excludes flicker/blink effects on your own permanents.

ROLE_PATTERNS: list[tuple[str, str, str]] = [

    # ── Ramp ──────────────────────────────────────────────────────────────────

    # permanent tap sources: mana rocks, mana dorks — "{T}: Add …"
    (r"\{T\}:\s*[Aa]dd", "ramp", "tap:mana"),
    # spell/ritual mana burst: explicit mana-symbol addition without a tap cost
    (r"\badd \{[WUBRGCXS]\}", "ramp", "spell:mana"),
    (r"\badd \{\d+\}", "ramp", "spell:mana"),
    (r"\badd [a-z]+ mana\b", "ramp", "spell:mana"),
    (r"\badd mana (of|in) (any|one|two|three)", "ramp", "spell:mana"),
    # land-to-battlefield: Rampant Growth, Cultivate, Kodama's Reach
    (r"search your library.{0,80}\bland cards?\b.{0,80}(battlefield|into play)", "ramp", "land:battlefield"),
    (
        r"search your library.{0,30}for (a |an |up to \w+ )?"
        r"(plains|island|swamp|mountain|forest|snow-covered|basic land)"
        r".{0,100}(battlefield|into play)",
        "ramp",
        "land:battlefield",
    ),
    # land-to-hand: Nature's Lore variants that put land in hand
    (
        r"search your library.{0,30}for (a |an |up to \w+ )?"
        r"(plains|island|swamp|mountain|forest|snow-covered|basic land)"
        r".{0,100}to your hand",
        "ramp",
        "land:hand",
    ),
    # direct land placement: "put a land onto the battlefield"
    (r"put (a|one|two|an?|the) (basic )?land.{0,40}(onto|into) (the )?battlefield", "ramp", "land:battlefield"),
    # additional land drops: Exploration, Azusa, Oracle of Mul Daya
    (r"play (one|two|three|x|an?)? ?additional lands? (each turn|this turn|per turn)?", "ramp", "extra_land_drop"),
    # mana doublers: Doubling Cube, Mana Flare
    (r"double (the amount of|your|all) mana", "ramp", "mana_double"),

    # ── Draw (one-time) ───────────────────────────────────────────────────────

    (r"\bdraw (a card|one card|two cards|three cards|four cards|five cards|six cards|seven cards|x cards?|\d+ cards?)\b", "draw_one", "spell:draw"),
    (
        r"\bdraw (a card|cards?).{0,40}discard (a card|cards?)"
        r"|\bdiscard.{0,30}draw (a card|cards?)\b",
        "draw_one",
        "spell:loot",
    ),
    (
        r"exile the top \S+ cards? of your library.{0,80}"
        r"(you may (play|cast)|may (play|cast) (it|them))",
        "draw_one",
        "spell:impulse",
    ),
    (
        r"exile the top.{0,60}put (that card|them|it).{0,30}into your hand"
        r"|pay \d+ life.{0,60}draw (a card|cards?)"
        r"|you lose \d+ life.{0,30}draw (a card|cards?)",
        "draw_one",
        "spell:draw",
    ),
    (
        r"look at the top.{0,40}put (one|two|\w+) of (them|those cards).{0,30}(your hand|into your hand)"
        r"|look at the top.{0,60}put (it|one of them|that card).{0,30}(your hand|into your hand)",
        "draw_one",
        "spell:impulse",
    ),
    (r"each (player|opponent).{0,60}draws? (cards?|a card|\d+ cards?|seven cards?|x cards?)", "draw_one", "spell:wheel"),

    # ── Repeatable draw ───────────────────────────────────────────────────────

    (r"\bwhenever\b.{0,120}draws? (a card|\d+ cards?|cards?)", "repeatable_draw", "triggered:draw"),
    (
        r"at the beginning of.{0,80}draws? (a card|\d+ cards?|cards?|an additional card|two additional)",
        "repeatable_draw",
        "upkeep:draw",
    ),
    (r"\{[^}]+\}[^:]{0,80}:\s*.{0,60}draw (a card|\d+ cards?|cards?)", "repeatable_draw", "activated:draw"),
    (
        r"at the beginning of.{0,60}"
        r"exile the top.{0,80}(you may (play|cast)|may (play|cast) (it|them))",
        "repeatable_draw",
        "upkeep:impulse",
    ),

    # ── Removal (single-target) ───────────────────────────────────────────────
    #
    # Compound "or" phrasings first, then single-type patterns.
    # (?! or ) prevents single-type patterns from matching inside compound phrases.
    # (?! you control) excludes blink/flicker effects on your own permanents.

    # destroy — compound phrasings
    (r"destroy target (creature or planeswalker|planeswalker or creature)(?! you control)", "removal", "destroy:creature_planeswalker"),
    (r"destroy target (artifact or enchantment|enchantment or artifact)(?! you control)", "removal", "destroy:artifact_enchantment"),
    (r"destroy target (creature or enchantment|enchantment or creature)(?! you control)", "removal", "destroy:creature_enchantment"),
    (r"destroy target (artifact or creature|creature or artifact)(?! you control)", "removal", "destroy:artifact_creature"),
    # destroy — single-type (broadest first so patterns remain mutually exclusive)
    (r"destroy target (\w+ )?permanent(?! or )(?! you control)", "removal", "destroy:permanent"),
    (r"destroy target (\w+ )?nonland permanent(?! or )(?! you control)", "removal", "destroy:nonland_permanent"),
    (r"destroy target (\w+ )?creature(?! or )(?! you control)", "removal", "destroy:creature"),
    (r"destroy target (\w+ )?artifact(?! or )(?! you control)", "removal", "destroy:artifact"),
    (r"destroy target (\w+ )?enchantment(?! or )(?! you control)", "removal", "destroy:enchantment"),
    (r"destroy target (\w+ )?planeswalker(?! or )(?! you control)", "removal", "destroy:planeswalker"),
    (r"destroy target (\w+ )?land(?! or )(?! you control)", "removal", "destroy:land"),
    (r"destroy target (\w+ )?token(?! or )(?! you control)", "removal", "destroy:token"),

    # exile — compound phrasings
    (r"exile target (creature or planeswalker|planeswalker or creature)(?! you control)", "removal", "exile:creature_planeswalker"),
    (r"exile target (artifact or enchantment|enchantment or artifact)(?! you control)", "removal", "exile:artifact_enchantment"),
    (r"exile target (creature or enchantment|enchantment or creature)(?! you control)", "removal", "exile:creature_enchantment"),
    # exile — single-type
    (r"exile target (\w+ )?permanent(?! or )(?! you control)", "removal", "exile:permanent"),
    (r"exile target (\w+ )?nonland permanent(?! or )(?! you control)", "removal", "exile:nonland_permanent"),
    (r"exile target (\w+ )?creature(?! or )(?! you control)", "removal", "exile:creature"),
    (r"exile target (\w+ )?artifact(?! or )(?! you control)", "removal", "exile:artifact"),
    (r"exile target (\w+ )?enchantment(?! or )(?! you control)", "removal", "exile:enchantment"),
    (r"exile target (\w+ )?planeswalker(?! or )(?! you control)", "removal", "exile:planeswalker"),
    (r"exile target (\w+ )?land(?! or )(?! you control)", "removal", "exile:land"),

    # burn / direct damage
    # "any target" = creature, planeswalker, or player — grouped as creature_planeswalker
    (
        r"deals? \w+ damage to any target"
        r"|deals? [Xx] damage to any target",
        "removal",
        "damage:any_target",
    ),
    (r"deals? \w+ damage to target (creature or planeswalker|planeswalker or creature)", "removal", "damage:creature_planeswalker"),
    (r"deals? \w+ damage to target (creature|player|opponent)(?! or )", "removal", "damage:creature"),

    # bounce — return to owner's hand (opponent's permanent, not yours)
    (r"return target (creature or planeswalker|planeswalker or creature)(?! you control).{0,40}to (its|their) owner's hand", "removal", "bounce:creature_planeswalker"),
    (r"return target (permanent|nonland permanent)(?! you control).{0,40}to (its|their) owner's hand", "removal", "bounce:permanent"),
    (r"return target creature(?! or )(?! you control).{0,40}to (its|their) owner's hand", "removal", "bounce:creature"),
    (r"return target (artifact|enchantment|planeswalker)(?! or )(?! you control).{0,40}to (its|their) owner's hand", "removal", "bounce:noncreature"),

    # -X/-X until end of turn (Dismember, Grasp of Darkness)
    (r"target.{0,30}gets? -\d+/-\d+ until end of turn", "removal", "reduce_toughness:creature"),

    # tuck — shuffle target into library (Chaos Warp, Spin into Myth)
    (
        r"(the owner of target|target \w+ permanent).{0,40}shuffles? it into (their|their owner's) library"
        r"|shuffles? target.{0,30}into (their|its owner's) library",
        "removal",
        "tuck:permanent",
    ),

    # ── Sweeper (mass removal) ────────────────────────────────────────────────

    (r"destroy all creatures", "sweeper", "destroy:all_creatures"),
    (r"destroy (all|each) (nonland permanents?|permanents?)", "sweeper", "destroy:all_permanents"),
    (r"destroy (all|each) (artifacts?|enchantments?|tokens?)", "sweeper", "destroy:all_artifacts"),
    (r"destroy each creature", "sweeper", "destroy:all_creatures"),
    (r"exile all creatures", "sweeper", "exile:all_creatures"),
    (r"exile (all|each) (nonland permanents?|permanents?)", "sweeper", "exile:all_permanents"),
    (r"exile each creature", "sweeper", "exile:all_creatures"),
    (r"deals? \w+ damage to (all|each) creature", "sweeper", "damage:all_creatures"),
    (
        r"return (all|each) (nonland permanents?|permanents?|creatures?|tokens?)"
        r".{0,40}(to (its|their|your).{0,10}hand|to their owners'? hand)",
        "sweeper",
        "bounce:all_permanents",
    ),
    (r"(all|each) creatures?.{0,30}(gets?|takes?|receives?).{0,30}-[\dxX]+/-[\dxX]+", "sweeper", "reduce_toughness:all_creatures"),
    (r"put.{0,30}-1/-1 counters? on (all|each) creature", "sweeper", "reduce_toughness:all_creatures"),
    (r"each (player|opponent) sacrifices (a creature|all creatures|creatures?)", "sweeper", "sacrifice:all_creatures"),
    # Overload spells: effect line precedes "Overload" keyword (Cyclonic Rift)
    (r"(return|destroy|exile) target[\s\S]{0,300}\boverload\b", "sweeper", "bounce:all_permanents"),

    # ── Tutor ─────────────────────────────────────────────────────────────────

    # Most specific types first; "any card" catch-all last.
    (
        r"search your library( and/or \w+)? for (a |an |up to (one|two|three) )?"
        r"(legendary )?creature card",
        "tutor",
        "creature",
    ),
    (
        r"search your library( and/or \w+)? for (a |an |up to (one|two|three) )?artifact card",
        "tutor",
        "artifact",
    ),
    (
        r"search your library( and/or \w+)? for (a |an |up to (one|two|three) )?enchantment card",
        "tutor",
        "enchantment",
    ),
    (
        r"search your library( and/or \w+)? for (a |an |up to (one|two|three) )?(instant|sorcery) card",
        "tutor",
        "instant_sorcery",
    ),
    (
        r"search your library( and/or \w+)? for (a |an |up to (one|two|three) )?planeswalker card",
        "tutor",
        "planeswalker",
    ),
    # generic "a card" / "basic land card" (includes land tutors not already caught by ramp)
    (
        r"search your library( and/or \w+)? for (a |an |up to (one|two|three) )?"
        r"(card|basic land card|plains|island|swamp|mountain|forest)",
        "tutor",
        "any_card",
    ),

    # ── Protection ────────────────────────────────────────────────────────────

    (r"\bhexproof\b", "protection", "hexproof"),
    (r"\bindestructible\b", "protection", "indestructible"),
    (r"\bshroud\b", "protection", "shroud"),
    (r"\bregenerate\b", "protection", "regenerate"),
    (r"\bphase out\b", "protection", "phase_out"),
    (r"\bprotection from (everything|all)\b", "protection", "protection_all"),

    # ── Win condition ─────────────────────────────────────────────────────────

    (r"\binfect\b", "win_condition", "infect"),
    (r"\btoxic \d\b", "win_condition", "toxic"),
    (r"\bpoison counter", "win_condition", "infect"),
    (r"(you |the )?(wins?|win) the game\b", "win_condition", "alt_win"),
    (r"that player (loses|lost) the game\b", "win_condition", "alt_win"),
    (r"each (opponent|player) loses the game\b", "win_condition", "alt_win"),
    (r"each opponent loses \d+ life.{0,40}you gain", "win_condition", "life_drain"),

    # ── Anthem ────────────────────────────────────────────────────────────────

    (
        r"(creatures? (tokens? )?(you control|in your command zone)"
        r"|other creatures you control) get \+\d+/[+\d]",
        "anthem",
        "static_pump",
    ),
    (r"each (creature you control|of your creatures) gets? \+\d+/[+\d]", "anthem", "static_pump"),
    # Coat of Arms / lord-style scaling pump
    (r"(gets?|get) \+\d+/[+\-\d]+.{0,50}for each (other|creature)", "anthem", "scaling_pump"),

    # ── Token generator ───────────────────────────────────────────────────────

    (r"create (a|an|one|two|three|four|five|six|x|\d+).{0,50}tokens?", "token_generator", "create_token"),
    # pre-M15 template
    (r"put (a|an|one|two|three|\d+).{0,50}token.{0,30}(onto|into) (the )?battlefield", "token_generator", "create_token"),

    # ── Recursion ─────────────────────────────────────────────────────────────

    (
        r"return (target )?.{0,60}card from (your|a|any) graveyard"
        r".{0,60}to (your hand|the battlefield|battlefield)",
        "recursion",
        "graveyard_to_hand",
    ),
    (
        r"put.{0,30}from (your|a|the) graveyard.{0,40}"
        r"(onto|into|to) (the )?battlefield",
        "recursion",
        "graveyard_to_battlefield",
    ),
    (r"enchant creature card in (a|the) graveyard", "recursion", "reanimate_aura"),
    (r"return (this card|it) from your graveyard", "recursion", "self_recursion"),
    (r"when.{0,60}dies.{0,60}return (it|that card|target creature)", "recursion", "death_trigger"),

    # ── Interaction (stack-based) ─────────────────────────────────────────────

    (r"counter target spell\b", "interaction", "hard_counter"),
    (
        r"counter target (noncreature|creature|instant|sorcery|enchantment|artifact|legendary)"
        r".{0,40}\bspell\b",
        "interaction",
        "conditional_counter",
    ),
    (r"counter target spell.{0,80}unless", "interaction", "conditional_counter"),
    (
        r"change the target.{0,40}target (spell|ability)"
        r"|choose new targets for target (spell|ability)",
        "interaction",
        "redirect",
    ),

    # ── Combat trick ──────────────────────────────────────────────────────────

    (r"(get(s)?|gain(s)?).{0,30}\+\d+/\+\d+.{0,30}until end of turn", "combat_trick", "pump"),
    (
        r"(gain(s)?|get(s)?|has|have).{0,60}"
        r"(trample|deathtouch|first strike|double strike|lifelink|haste|vigilance)"
        r".{0,40}until end of turn",
        "combat_trick",
        "keyword_grant",
    ),
    (
        r"(gain(s)?|get(s)?|has|have).{0,60}"
        r"(flying|menace|shadow|fear|intimidate|skulk|horsemanship|unblockable)"
        r".{0,40}until end of turn"
        r"|can't be blocked.{0,30}(until end of turn|this turn)"
        r"|\bis unblockable\b",
        "combat_trick",
        "evasion_grant",
    ),

    # ── Aura / Equipment ──────────────────────────────────────────────────────

    # Equipment: any card with an equip cost IS an equipment
    (r"\bequip \{", "aura_equipment", "equipment"),
    # Aura: targets a creature or permanent on the battlefield
    (r"\benchant (creature|permanent)\b", "aura_equipment", "aura"),

    # ── ETB trigger ───────────────────────────────────────────────────────────

    (
        r"whenever (a |one or more )?creatures? (enters?|enter) the battlefield"
        r" (under your control|you control)",
        "etb_trigger",
        "etb_payoff",
    ),
    (r"whenever (a |one or more )?creatures? you control enters?", "etb_trigger", "etb_payoff"),

    # ── Wide payoff ───────────────────────────────────────────────────────────

    # Damage that scales with creature count (Impact Tremors, Warstorm Surge)
    (r"deals? \w+ damage.{0,60}for each (creature|token)", "wide_payoff", "damage_per_creature"),
    # Any value that scales with creature/token count (draw, life, etc.)
    (r"for each (creature|token) (you control|on the battlefield)", "wide_payoff", "count_matters"),
    (r"equal to (the number of|x, where x is the number of).{0,40}creature", "wide_payoff", "count_matters"),

    # ── Sac outlet ────────────────────────────────────────────────────────────

    # Activated ability with sacrifice as a cost (Goblin Bombardment, Viscera Seer)
    (r"sacrifice (a |an )?(creature|token|goblin|elf|zombie)\s*[,:]", "sac_outlet", "sac_cost"),
    (r"\{[^}]*\},?\s*sacrifice (a|an)? (creature|token)\b", "sac_outlet", "sac_cost"),

    # ── Discard trigger ───────────────────────────────────────────────────────

    (r"whenever you discard", "discard_trigger", "discard_payoff"),
    (r"whenever (a card is discarded|a player discards)", "discard_trigger", "discard_payoff"),
]


def is_land_card(type_line: str) -> bool:
    """Return True if *type_line* indicates the card is a Land.

    Shared by ``pipeline.py`` and the tests to avoid duplicating the check.
    """
    return "land" in type_line.lower()


# ── Land-specific role patterns ───────────────────────────────────────────────
# Applied ONLY when is_land_card(type_line) is True.

LAND_ROLE_PATTERNS: list[tuple[str, str, str]] = [
    # mana_land: lands that tap to produce mana
    (r"\{T\}:\s*[Aa]dd", "mana_land", "tap:mana"),
    # utility_land: fetchlands that search for a specific land by type name or "land card"
    (
        r"search your library for (a |an |up to \w+ )?"
        r"(basic )?("
        r"land card?|plains|island|swamp|mountain|forest"
        r"|snow-covered|nonbasic land)",
        "utility_land",
        "land_tutor",
    ),
    # utility_land: search for two specific land types (e.g. "a Plains or Forest card")
    (
        r"search your library for (a |an )?"
        r"(plains|island|swamp|mountain|forest).{0,20} or "
        r"(plains|island|swamp|mountain|forest)",
        "utility_land",
        "land_tutor",
    ),
    # non-mana activated abilities on lands (Maze of Ith, Bojuka Bog style)
    (
        r"\{T\}.{0,10}:(?!.{0,10}[Aa]dd).{0,60}(exile|untap|prevent|search|draw|create|destroy)",
        "utility_land",
        "activated:non_mana",
    ),
]
