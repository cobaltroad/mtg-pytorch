"""Archetype engine synergy patterns and producer SQL fragments.

Covers commander-agnostic card engines that define a deck's archetype rather
than filling a universal support role (removal, draw, tutors — those live in
utility.py).  Each pattern identifies a mini-combo or self-contained synergy
loop that works regardless of commander choice:

  skullclamp_target — X/1 token generators → Skullclamp (toughness-drain mini-combo)
  graveyard_return  — reanimation payoffs; producers are reanimate spells + mill
  graveyard_fill    — threshold / delirium / morbid payoffs; producers are mill / loot
  artifact_matters  — artifact-payoff consumers; producers are any artifact + treasure/
                      food/blood/clue token generators
  modified          — "modified" super-type payoffs (counters + auras + equipment);
                      producers are any of the three enabler types
  aura_matters      — enchanted-creature payoffs + enchantress triggers; producers are
                      aura enchantments + enchantress effects + aura tutors
  enchantress        — draw-when-enchantment-cast payoffs (Sythis, Argothian Enchantress,
                       Eidolon of Blossoms); producers are any enchantment
  play_from_exile   — cast-from-exile / cascade / impulse-draw payoffs; producers are
                      cards that exile and allow casting (cascade, discover, impulse draw,
                      airbend)

These edges are written with score_type='card_synergy' by compute_card_synergy()
so they flow into the compositional dataset artifact (mtg_dataset.pt) rather than
the commander artifact (mtg_commanders.pt).
"""

from __future__ import annotations

# ── Trigger patterns ──────────────────────────────────────────────────────────

TRIGGER_PATTERNS: list[tuple[str, str, str]] = [
    # Skullclamp proxy: equipment that drains toughness to zero on a 1/1 → dies, draw 2
    (r"equipped creature gets \+\S+/-1", "Skullclamp toughness-drain", "skullclamp_target"),

    # Reanimator: cards with graveyard-activated abilities or that return creatures from graveyards
    (
        r"you may (cast|activate) .{0,40}(from|in) (your |a |the )?graveyard"
        r"|activate .{0,40}only from (your )?graveyard"
        r"|when(ever)?\s+.{0,40}return(s|ed)?.{0,20}from (your |a |the )?graveyard"
        r"|from (your|a) graveyard (to the battlefield|to play)",
        "Graveyard return trigger",
        "graveyard_return",
    ),

    # Graveyard fill: threshold / delirium / morbid keywords + cards-in-graveyard triggers
    (
        r"\b(threshold|delirium|morbid)\b"
        r"|when(ever)?\s+(a |any )?card.{0,30}(put into|enters?).{0,15}graveyard",
        "Graveyard fill trigger",
        "graveyard_fill",
    ),

    # Artifacts matter: casting artifacts, artifact payoffs, artifact token payoffs
    (
        r"when(ever)?\s+(you )?cast an artifact spell"
        r"|whenever (a |an )?artifact (enters?|is created)"
        r"|artifacts?.{0,10}you control.{0,30}(get|gain|have)"
        r"|artifact (creatures?|tokens?).{0,20}you control",
        "Artifact matters",
        "artifact_matters",
    ),

    # Modified: super-type for (counter|aura|equip) — covers all three sub-themes
    (
        r"\bmodified\b"
        r"|creatures? .{0,30}(counter|aura|equip).{0,30}(get|gain|have|are|attached)"
        r"|auras? and equipment"
        r"|(enchantment|aura).{0,10}(and|or).{0,10}(equipment|artifact)",
        "Modified trigger",
        "modified",
    ),

    # Aura matters: enchanted-creature payoffs, enchantress triggers, auto-attach auras
    (
        r"enchanted creature (gets?|gains?|has|deals?)"
        r"|when(ever)?\s+(an )?(aura|enchantment).{0,30}(enters?|you cast|attaches?)"
        r"|auras? you control.{0,20}(get|give|have)"
        r"|when .{0,30}becomes? enchanted"
        r"|when(ever)?\s+(you )?cast an enchantment"
        r"|enchantments? you control.{0,30}(get|gain|have)",
        "Aura matters",
        "aura_matters",
    ),

    # Enchantress: draw a card whenever you cast an enchantment spell or an
    # enchantment enters the battlefield.  More focused than aura_matters
    # (which includes non-draw enchantment payoffs); produces edges directly
    # from enchantment cards → enchantress-draw permanents.
    # Consumers: Sythis Harvest's Hand, Argothian Enchantress, Eidolon of
    # Blossoms, Setessan Champion, Enchantress's Presence.
    (
        r"when(ever)?\s+(you cast an? (enchantment|aura) spell"
        r"|an enchantment enters?( the battlefield)?( under your control)?)"
        r".{0,100}draw (a card|cards?)",
        "Enchantress draw trigger",
        "enchantress",
    ),

    # Play-from-exile / impulse-draw payoffs
    # Covers: cast-from-exile triggers (Laelia, Birgi/Harnfel), the paradox keyword and
    # its explicit wording ("from anywhere other than your hand") used in the Dr Who set
    # (e.g. The Thirteenth Doctor), cascade/discover payoffs (Faldorn Dread Wolf Herald,
    # Abaddon the Despoiler), indirect cascade payoffs that count spells or ETBs this turn
    # (Noise Marine, Let The Galaxy Burn), and any card that explicitly rewards casting
    # from exile.
    (
        r"when(ever)?\s+(you )?cast .{0,60}from exile"
        r"|when(ever)?\s+(you )?cast .{0,60}exiled (this way|with )"
        r"|\bparadox\b"
        r"|when(ever)?\s+(you )?cast .{0,70}from anywhere other than your hand"
        r"|when(ever)?\s+(you )?cast .{0,60}(a spell with cascade|a cascading spell)"
        r"|when(ever)?\s+(you )?cast .{0,60}\bwith cascade\b"
        # Indirect cascade/impulse payoffs: count-based triggers driven by casting many spells
        r"|number of (instant|sorcery|spells?).{0,40}(you've |you have )?cast this turn"
        r"|creatures?.{0,60}entered the battlefield this turn",
        "Play from exile / cascade payoff",
        "play_from_exile",
    ),
]

# ── Card synergy map ──────────────────────────────────────────────────────────
# Keyed by trigger_event (matches card_abilities rows written by tag_abilities).
# Written as score_type='card_synergy' by compute_card_synergy() so these edges
# flow into the compositional dataset artifact, not the commander artifact.

CARD_SYNERGY_MAP: dict[str, str] = {
    # 1-toughness token generators — natural Skullclamp targets (proxy/indirect synergy edge).
    # Only toughness matters: any X/1 token dies immediately when Skullclamp's -1 toughness is applied.
    "skullclamp_target": (
        "lower(oracle_text) LIKE '%create%/1 %token%'"
        " OR lower(oracle_text) LIKE '%creates%/1 %token%'"
        " OR lower(oracle_text) LIKE '%put a%/1 %token%'"
        " OR lower(oracle_text) LIKE '%put%/1%creature token%'"
    ),

    # Reanimator: spells that return creatures from the graveyard
    # + cards with graveyard-activated abilities (Unearth, Flashback, Escape, etc.)
    # + mill — fills the graveyard making reanimation viable
    "graveyard_return": (
        # Classic reanimation spells (Reanimate, Animate Dead, Resurrection, etc.)
        "lower(oracle_text) LIKE '%return target%creature%graveyard%battlefield%'"
        " OR lower(oracle_text) LIKE '%creature card from%graveyard%battlefield%'"
        " OR lower(oracle_text) LIKE '%creature card from a graveyard%battlefield%'"
        " OR lower(oracle_text) LIKE '%put target%creature%graveyard%battlefield%'"
        " OR lower(oracle_text) LIKE '%return%from your graveyard to the battlefield%'"
        # Graveyard-activated abilities (Unearth, Escape, Flashback, etc.)
        " OR lower(oracle_text) LIKE '%unearth%'"
        " OR lower(oracle_text) LIKE '%escape%'"
        " OR lower(oracle_text) LIKE '%flashback%'"
        " OR lower(oracle_text) LIKE '%you may cast%from your graveyard%'"
        " OR lower(oracle_text) LIKE '%activate%only from%graveyard%'"
        # Mill fills the graveyard, enabling reanimation
        " OR lower(oracle_text) LIKE '%mill%'"
        " OR lower(oracle_text) LIKE '%put the top%card%into%graveyard%'"
    ),

    # Graveyard fill: mill, surveil, dredge, loot — feeds threshold/delirium/morbid payoffs
    "graveyard_fill": (
        "lower(oracle_text) LIKE '%mill%'"
        " OR lower(oracle_text) LIKE '%put the top%card%graveyard%'"
        " OR lower(oracle_text) LIKE '%surveil%'"
        " OR lower(oracle_text) LIKE '%dredge%'"
        " OR lower(oracle_text) LIKE '%draw a card, then discard%'"
        " OR lower(oracle_text) LIKE '%discard a card%draw%'"
        " OR lower(oracle_text) LIKE '%each player discards%'"
    ),

    # Artifacts matter: artifact cards as producers for artifact-payoff consumers.
    # Includes vehicles, equipment, treasure/food/blood/clue/junk/mutagen token producers.
    "artifact_matters": (
        "lower(type_line) LIKE '%artifact%'"
        " OR lower(oracle_text) LIKE '%create%treasure%'"
        " OR lower(oracle_text) LIKE '%create%food%'"
        " OR lower(oracle_text) LIKE '%create%blood%'"
        " OR lower(oracle_text) LIKE '%create%clue%'"
        " OR lower(oracle_text) LIKE '%create%junk%'"
        " OR lower(oracle_text) LIKE '%create%mutagen%'"
    ),

    # Modified: counters + auras + equipment attached to a creature
    "modified": (
        "lower(type_line) LIKE '%equipment%'"
        " OR (lower(type_line) LIKE '%enchantment%' AND lower(oracle_text) LIKE '%enchant creature%')"
        " OR lower(oracle_text) LIKE '%put a +1/+1 counter%'"
        " OR lower(oracle_text) LIKE '%proliferate%'"
        " OR lower(oracle_text) LIKE '%attach%equipment%'"
    ),

    # Aura matters: aura enchantments, enchantress effects, and auto-attach auras
    "aura_matters": (
        # Aura cards themselves
        "(lower(type_line) LIKE '%enchantment%' AND lower(oracle_text) LIKE '%enchant creature%')"
        # Enchantress / all-enchantments-matter effects
        " OR lower(oracle_text) LIKE '%whenever (an )?enchantment enters%'"
        " OR lower(oracle_text) LIKE '%whenever you cast an enchantment%'"
        " OR lower(oracle_text) LIKE '%enchantments you control%'"
        " OR lower(oracle_text) LIKE '%number of enchantments%'"
        # Aura tutors and recursion
        " OR lower(oracle_text) LIKE '%return%aura%from%graveyard%'"
        " OR lower(oracle_text) LIKE '%search your library for%aura%'"
        " OR lower(oracle_text) LIKE '%search your library for an enchantment%'"
        # Auto-attach (Eldrazi Conscription / totem armor style)
        " OR (lower(type_line) LIKE '%enchantment%' AND lower(oracle_text) LIKE '%enters the battlefield attached%')"
    ),

    # Enchantress draw payoffs are triggered by casting or playing enchantment
    # cards.  Any enchantment is a valid producer.
    "enchantress": (
        "lower(type_line) LIKE '%enchantment%'"
    ),

    # Play-from-exile producers: cards that create windows to cast from exile.
    # Includes impulse-draw effects (exile top of library + "you may play this turn"),
    # cascade (exile until a lower-CMC card is found and cast it), discover (similar
    # to cascade), the airbend mechanic (exiles a card or permanent from play/stack
    # for re-casting), and any other "exile and cast/play" mechanics.
    "play_from_exile": (
        # Impulse draw: exile top of library with a timed "you may play/cast" window
        "lower(oracle_text) LIKE '%exile the top%you may%play%'"
        " OR lower(oracle_text) LIKE '%exile the top%you may%cast%'"
        " OR lower(oracle_text) LIKE '%exile%top%card%you may%play%'"
        " OR lower(oracle_text) LIKE '%exile%top%card%you may%cast%'"
        # Timed windows expressed as "until end of turn" or "this turn"
        " OR lower(oracle_text) LIKE '%exile%you may play%this turn%'"
        " OR lower(oracle_text) LIKE '%exile%you may play%until%'"
        " OR lower(oracle_text) LIKE '%exile%you may cast%this turn%'"
        " OR lower(oracle_text) LIKE '%exile%you may cast%until%'"
        # Cascade keyword (exiles until lower-CMC card found, then casts it for free)
        " OR 'Cascade' = ANY(keywords)"
        # Discover keyword (similar to cascade; exile until you find CMC ≤ N, cast for free)
        " OR 'Discover' = ANY(keywords)"
        # Airbend mechanic (TLA set): exiles a card from the battlefield/stack;
        # owner may cast it for as long as it remains exiled
        " OR 'Airbend' = ANY(keywords)"
        # Hand-exile with optional recast: "exile target card from a player's hand...
        # that card's owner may cast it" (Elite Spellbinder style).
        # Requires 'that card' after the exile/hand clause to ensure both clauses
        # refer to the same exiled card (avoids false positives where 'exile' and
        # 'may cast' appear in unrelated ability sentences).
        " OR (lower(oracle_text) LIKE '%exile%from%hand%that card%' AND lower(oracle_text) LIKE '%may cast%')"
    ),
}
