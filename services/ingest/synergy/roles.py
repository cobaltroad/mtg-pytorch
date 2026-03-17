"""Functional role patterns for card_abilities tagging.

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
# Each entry: (oracle_regex, role_name)
# Regexes are matched with re.IGNORECASE and re.DOTALL is NOT used — patterns
# must match within a single oracle-text segment (separated by '\n' for
# double-faced / modal cards).  Use [\s\S] or (?s) if cross-line matching is
# required for a specific pattern.

ROLE_PATTERNS: list[tuple[str, str]] = [

    # ── Ramp ──────────────────────────────────────────────────────────────────

    # mana rocks / dorks: "{T}: Add …" is the universal template
    (r"\{T\}:\s*[Aa]dd", "ramp"),
    # explicit "add {X}" mana symbols: Sol Ring, Llanowar Elves, etc.
    (r"\badd \{[WUBRGCXS]\}", "ramp"),
    (r"\badd \{\d+\}", "ramp"),
    # variable-mana producers: "add X mana", "add mana of any color", etc.
    (r"\badd [a-z]+ mana\b", "ramp"),
    (r"\badd mana (of|in) (any|one|two|three)", "ramp"),
    # land-to-battlefield tutors: Rampant Growth, Cultivate, Kodama's Reach
    (r"search your library.{0,80}\bland cards?\b.{0,80}(battlefield|into play)", "ramp"),
    # basic land type search: Farseek, Nature's Lore, Into the North
    # Allow "and/or graveyard" and "up to N" wordings; destination up to 100 chars away
    (
        r"search your library.{0,30}for (a |an |up to \w+ )?"
        r"(plains|island|swamp|mountain|forest|snow-covered|basic land)"
        r".{0,100}(battlefield|into play|to your hand)",
        "ramp",
    ),
    # put a land onto the battlefield directly
    (r"put (a|one|two|an?|the) (basic )?land.{0,40}(onto|into) (the )?battlefield", "ramp"),
    # play additional land(s): Exploration, Azusa, Oracle of Mul Daya
    (r"play (one|two|three|x|an?)? ?additional lands? (each turn|this turn|per turn)?", "ramp"),
    # Doubling Cube, Mana Flare type effects
    (r"double (the amount of|your|all) mana", "ramp"),

    # ── Draw (one-time) ───────────────────────────────────────────────────────

    # direct draw: "draw a card", "draw two cards", "draw X cards"
    (r"\bdraw (a card|one card|two cards|three cards|four cards|five cards|six cards|seven cards|x cards?|\d+ cards?)\b", "draw_one"),
    # loot: draw then discard (or discard then draw)
    (
        r"\bdraw (a card|cards?).{0,40}discard (a card|cards?)"
        r"|\bdiscard.{0,30}draw (a card|cards?)\b",
        "draw_one",
    ),
    # impulse draw: exile top of library then may play/cast it (one-time spell)
    (
        r"exile the top \S+ cards? of your library.{0,80}"
        r"(you may (play|cast)|may (play|cast) (it|them))",
        "draw_one",
    ),
    # Necropotence / Dark Confidant style: exile/reveal top and put to hand
    (
        r"exile the top.{0,60}put (that card|them|it).{0,30}into your hand"
        r"|pay \d+ life.{0,60}draw (a card|cards?)"
        r"|you lose \d+ life.{0,30}draw (a card|cards?)",
        "draw_one",
    ),
    # "Look at the top X cards … put one/them into your hand" (Scroll Rack, Impulse, etc.)
    (
        r"look at the top.{0,40}put (one|two|\w+) of (them|those cards).{0,30}(your hand|into your hand)"
        r"|look at the top.{0,60}put (it|one of them|that card).{0,30}(your hand|into your hand)",
        "draw_one",
    ),
    # wheel / mass-draw: all players draw simultaneously (Windfall, Wheel of Fortune,
    # Jace's Archivist, Reforge the Soul).  These are one-shot effects, typically cast
    # from hand, even though they affect all players.
    # "\d+ cards?" covers numeric amounts; "seven cards" / "x cards" cover named amounts.
    (r"each (player|opponent).{0,60}draws? (cards?|a card|\d+ cards?|seven cards?|x cards?)", "draw_one"),

    # ── Repeatable draw ───────────────────────────────────────────────────────
    # Static, permanent-based card-advantage engines that keep working as long as
    # the card remains on the battlefield.

    # "Whenever" triggered draw — fires repeatedly on a recurring condition.
    # Uses \bwhenever\b (not "when") to exclude single-fire ETB triggers like
    # "When Mulldrifter enters the battlefield, draw two cards."
    # Covers: Rhystic Study, Mystic Remora, Beast Whisperer, Consecrated Sphinx,
    #         Toski Bearer of Secrets, Reconnaissance Mission, etc.
    (r"\bwhenever\b.{0,120}draws? (a card|\d+ cards?|cards?)", "repeatable_draw"),
    # "at the beginning of" step-triggered draw — once per turn, every turn.
    # Covers: Phyrexian Arena, Sylvan Library, Howling Mine, Dictate of Kruphix.
    (
        r"at the beginning of.{0,80}draws? (a card|\d+ cards?|cards?|an additional card|two additional)",
        "repeatable_draw",
    ),
    # Activated-ability draw — explicit cost → draw on a permanent.
    # Covers: War Room ("{3}, {T}, Pay 1 life: Draw a card"),
    #         Sensei's Divining Top ("{1}, {T}: Draw three cards …"),
    #         The Immortal Sun, etc.
    # Matches any activation cost (one or more {symbol} groups) followed by ": draw".
    (
        r"\{[^}]+\}[^:]{0,80}:\s*.{0,60}draw (a card|\d+ cards?|cards?)",
        "repeatable_draw",
    ),
    # Repeatable impulse engine — at beginning of turn, exile top and may play.
    # Covers: Precognition Field, Future Sight, Oracle of Mul Daya.
    (
        r"at the beginning of.{0,60}"
        r"exile the top.{0,80}(you may (play|cast)|may (play|cast) (it|them))",
        "repeatable_draw",
    ),

    # ── Removal (single-target) ───────────────────────────────────────────────

    # destroy target permanent/creature/artifact/enchantment/planeswalker
    # "non-" prefixes (nonblack, nonwhite, nonartifact, nonland, etc.) covered by \w+
    (
        r"destroy target (\w+ )?(creature|permanent|artifact|enchantment|planeswalker"
        r"|nonland permanent|land|token)",
        "removal",
    ),
    # exile target permanent
    (
        r"exile target (\w+ )?(creature|permanent|artifact|enchantment|planeswalker"
        r"|nonland permanent|land|token)",
        "removal",
    ),
    # burn / direct damage to a single target
    (
        r"deals? \w+ damage to (any target|target (creature|player|opponent|planeswalker))"
        r"|deals? [Xx] damage to (any target|target)",
        "removal",
    ),
    # bounce: return target permanent to hand
    (
        r"return target (creature|permanent|nonland permanent|artifact|enchantment|planeswalker)"
        r".{0,40}(to its owner's hand|to their owner's hand|to your hand)",
        "removal",
    ),
    # -X/-X until end of turn on a target (wither-like)
    (r"target.{0,30}gets? -\d+/-\d+ until end of turn", "removal"),
    # library-shuffle removal: Chaos Warp, Spin into Myth
    (
        r"(the owner of target|target \w+ permanent).{0,40}shuffles? it into (their|their owner's) library"
        r"|shuffles? target.{0,30}into (their|its owner's) library",
        "removal",
    ),

    # ── Sweeper (mass removal) ────────────────────────────────────────────────

    # destroy/exile all creatures/permanents
    (
        r"destroy (all|each) (creatures?|permanents?|nonland permanents?"
        r"|artifacts?|enchantments?|tokens?)",
        "sweeper",
    ),
    (
        r"exile (all|each) (creatures?|permanents?|nonland permanents?"
        r"|artifacts?|enchantments?|tokens?)",
        "sweeper",
    ),
    # deal damage to all creatures (Blasphemous Act, Earthquake, etc.)
    (r"deals? \w+ damage to (all|each) creature", "sweeper"),
    # mass bounce (Evacuation, Cyclonic Rift overload)
    (
        r"return (all|each) (nonland permanents?|permanents?|creatures?|tokens?)"
        r".{0,40}(to (its|their|your).{0,10}hand|to their owners'? hand)",
        "sweeper",
    ),
    # -X/-X to all creatures (Toxic Deluge, Black Sun's Zenith)
    (r"(all|each) creatures?.{0,30}(gets?|takes?|receives?).{0,30}-[\dxX]+/-[\dxX]+", "sweeper"),
    (r"put.{0,30}-1/-1 counters? on (all|each) creature", "sweeper"),
    # each player sacrifices (Dictate of Erebos, Grave Pact broad effects)
    (r"each (player|opponent) sacrifices (a creature|all creatures|creatures?)", "sweeper"),
    # Overload on a bounce/removal spell: in oracle text the effect line precedes
    # the Overload keyword, so "return/destroy/exile target … Overload" means the
    # card CAN clear the whole board when overloaded (Cyclonic Rift).
    # Use [\s\S] to allow the match to span the newline between the effect and
    # the Overload reminder line.
    (
        r"(return|destroy|exile) target[\s\S]{0,300}\boverload\b",
        "sweeper",
    ),

    # ── Tutor ─────────────────────────────────────────────────────────────────

    # search library (and/or graveyard) for any named card type
    # Handles: "search your library for a card", "search your library and/or graveyard for a creature card"
    (
        r"search your library( and/or \w+)? for (a |an |up to (one|two|three) )?"
        r"(card|creature card|artifact card|land card|enchantment card"
        r"|instant card|sorcery card|legendary card|basic land card"
        r"|plains|island|swamp|mountain|forest|planeswalker card)",
        "tutor",
    ),

    # ── Protection ────────────────────────────────────────────────────────────

    # static or granted keywords
    (r"\bhexproof\b", "protection"),
    (r"\bindestructible\b", "protection"),
    (r"\bshroud\b", "protection"),
    (r"\bregenerate\b", "protection"),
    # phase out / protection from everything
    (r"\bphase out\b", "protection"),
    (r"\bprotection from (everything|all)\b", "protection"),

    # ── Win condition ─────────────────────────────────────────────────────────

    # infect / toxic (10 poison counters = loss)
    (r"\binfect\b", "win_condition"),
    (r"\btoxic \d\b", "win_condition"),
    (r"\bpoison counter", "win_condition"),
    # explicit win-the-game text
    (r"(you |the )?(wins?|win) the game\b", "win_condition"),
    (r"that player (loses|lost) the game\b", "win_condition"),
    (r"each (opponent|player) loses the game\b", "win_condition"),
    # commander damage (21 commander damage wins): no oracle text marker, skip
    # life-total drain as win mechanism (Exquisite Blood + Sanguine Bond style)
    (r"each opponent loses \d+ life.{0,40}you gain", "win_condition"),

    # ── Anthem ────────────────────────────────────────────────────────────────

    # global +N/+N to your creatures (includes "tokens" qualifier: Intangible Virtue)
    (
        r"(creatures? (tokens? )?(you control|in your command zone)"
        r"|other creatures you control) get \+\d+/[+\d]",
        "anthem",
    ),
    (r"each (creature you control|of your creatures) gets? \+\d+/[+\d]", "anthem"),
    # Coat of Arms / lord-style: "each … gets +1/+1 for each other …"
    # Also handles Shared Animosity "+1/+0 … for each other attacking creature"
    (r"(gets?|get) \+\d+/[+\-\d]+.{0,50}for each (other|creature)", "anthem"),

    # ── Token generator ───────────────────────────────────────────────────────

    # "create … token" is the universal template since M15
    (
        r"create (a|an|one|two|three|four|five|six|x|\d+).{0,50}tokens?",
        "token_generator",
    ),
    # older "put … token" template (pre-M15 sets)
    (
        r"put (a|an|one|two|three|\d+).{0,50}token.{0,30}(onto|into) (the )?battlefield",
        "token_generator",
    ),

    # ── Recursion ─────────────────────────────────────────────────────────────

    # return a card/creature from graveyard to hand or battlefield
    (
        r"return (target )?.{0,60}card from (your|a|any) graveyard"
        r".{0,60}(to (your hand|the battlefield|battlefield))",
        "recursion",
    ),
    # put directly from graveyard to battlefield
    (
        r"put.{0,30}from (your|a|the) graveyard.{0,40}"
        r"(onto|into|to) (the )?battlefield",
        "recursion",
    ),
    # exile from graveyard then return (Animate Dead / Dance of the Dead)
    (
        r"enchant creature card in (a|the) graveyard",
        "recursion",
    ),
    # "return ~ from your graveyard" self-recursion
    (
        r"return (this card|it) from your graveyard",
        "recursion",
    ),
    # triggered return: "whenever X dies, return it"
    (
        r"when.{0,60}dies.{0,60}return (it|that card|target creature)",
        "recursion",
    ),

    # ── Interaction (stack-based) ─────────────────────────────────────────────

    # hard counterspell
    (r"counter target spell\b", "interaction"),
    # type-conditional counterspell
    (
        r"counter target (noncreature|creature|instant|sorcery|enchantment|artifact|legendary)"
        r".{0,40}\bspell\b",
        "interaction",
    ),
    # "unless its controller pays" conditional counter
    (r"counter target spell.{0,80}unless", "interaction"),
    # redirect / change targets
    (
        r"change the target.{0,40}target (spell|ability)"
        r"|choose new targets for target (spell|ability)",
        "interaction",
    ),

    # ── Combat trick ──────────────────────────────────────────────────────────

    # pump effects (+X/+X until end of turn)
    (
        r"(get(s)?|gain(s)?).{0,30}\+\d+/\+\d+.{0,30}until end of turn",
        "combat_trick",
    ),
    # combat keyword grants until end of turn
    (
        r"(gain(s)?|get(s)?|has|have).{0,60}"
        r"(trample|deathtouch|first strike|double strike|lifelink|haste|vigilance)"
        r".{0,40}until end of turn",
        "combat_trick",
    ),
    # evasion grants
    (
        r"(gain(s)?|get(s)?|has|have).{0,60}"
        r"(flying|menace|shadow|fear|intimidate|skulk|horsemanship|unblockable)"
        r".{0,40}until end of turn"
        r"|can't be blocked.{0,30}(until end of turn|this turn)"
        r"|\bis unblockable\b",
        "combat_trick",
    ),
]


def is_land_card(type_line: str) -> bool:
    """Return True if *type_line* indicates the card is a Land.

    Shared by ``pipeline.py`` and the tests to avoid duplicating the check.
    """
    return "land" in type_line.lower()


# ── Land-specific role patterns ───────────────────────────────────────────────
# Applied ONLY when is_land_card(type_line) is True.

LAND_ROLE_PATTERNS: list[tuple[str, str]] = [
    # mana_land: lands that tap to produce mana
    (r"\{T\}:\s*[Aa]dd", "mana_land"),
    # utility_land: fetchlands that search for a specific land by type name or "land card"
    # Handles both "search your library for a Swamp or Forest card" (fetchlands) and
    # "search your library for a basic land card" (generic).
    (
        r"search your library for (a |an |up to \w+ )?"
        r"(basic )?("
        r"land card?|plains|island|swamp|mountain|forest"
        r"|snow-covered|nonbasic land)",
        "utility_land",
    ),
    # utility_land: search for two specific land types (e.g. "a Plains or Forest card")
    (
        r"search your library for (a |an )?"
        r"(plains|island|swamp|mountain|forest).{0,20} or "
        r"(plains|island|swamp|mountain|forest)",
        "utility_land",
    ),
    # non-mana activated abilities on lands (Maze of Ith, Bojuka Bog style)
    (
        r"\{T\}.{0,10}:(?!.{0,10}[Aa]dd).{0,60}(exile|untap|prevent|search|draw|create|destroy)",
        "utility_land",
    ),
]
