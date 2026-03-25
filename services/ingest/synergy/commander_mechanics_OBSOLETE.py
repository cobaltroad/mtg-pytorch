"""Producer SQL fragments for commander-specific mechanic pattern keys.

Companion to ``scripts/decompose_commanders.py``, which is the pipeline stage
that detects these mechanics and writes ``card_abilities`` rows
(``source='decompose'``) so that ``compute_synergy`` can build
``ability_trigger`` edges for commanders.

``synergy/__init__.py`` merges ``PATTERN_KEY_TO_PRODUCER_SQL`` into
``PRODUCER_MAP``; existing sub-modules (``events.py``, ``utility.py``,
etc.) take precedence for any keys they already handle more precisely.

``decompose_commanders.py`` also reads this SQL vocabulary directly for gap
analysis — ``--no-signals`` output identifies commanders whose mechanics lack
producer coverage.

Two families of keys are covered:

Oracle text keys
    Emitted by the regex patterns in ``scripts/decompose_commanders.py``.
    Examples: ``etb_trigger``, ``death_trigger``, ``equipment_matters``.

XMage ability-class keys
    Emitted by ``ABILITY_CLASS_TO_EVENT`` / ``SPELLCAST_FILTER_MAP`` in
    ``xmage_parse.py``.  Examples: ``creature_etb``, ``dies``, ``attacks``,
    ``historic_cast``.  Many overlap with oracle text keys; they are listed
    explicitly here for completeness and may have slightly different SQL.

All fragments are plain SQL WHERE bodies (no leading WHERE keyword).  They
reference only columns present in the ``cards`` table: ``type_line``,
``oracle_text``, ``keywords`` (text[]), ``cmc``.
"""

from __future__ import annotations

# ── Helper: combine OR clauses cleanly ────────────────────────────────────────


def _or(*clauses: str) -> str:
    joined = "\n        OR ".join(c.strip() for c in clauses if c.strip())
    return f"(\n        {joined}\n    )"


# ── Producer SQL map ──────────────────────────────────────────────────────────

PATTERN_KEY_TO_PRODUCER_SQL: dict[str, str] = {

    # ── ETB triggers ──────────────────────────────────────────────────────────
    # Commander fires when creatures/permanents enter.
    # Producers: creatures whose primary value is their ETB ability + blink effects.
    "etb_trigger": _or(
        "lower(oracle_text) LIKE '%when this creature enters%'",
        "lower(oracle_text) LIKE '%when % enters the battlefield%'",
        # ETB doublers (Panharmonicon-style)
        "lower(oracle_text) LIKE '%triggers an additional time%'",
        "lower(oracle_text) LIKE '%whenever a creature%enters%trigger%'",
        # Blink effects — reuse ETBs
        "(lower(oracle_text) LIKE '%exile%' AND lower(oracle_text) LIKE '%return%' "
        " AND lower(oracle_text) LIKE '%battlefield%' AND lower(oracle_text) LIKE '%under your control%')",
    ),

    # XMage alias
    "creature_etb": _or(
        "lower(oracle_text) LIKE '%when this creature enters%'",
        "lower(oracle_text) LIKE '%when % enters the battlefield%'",
        "lower(oracle_text) LIKE '%triggers an additional time%'",
        "(lower(oracle_text) LIKE '%exile%' AND lower(oracle_text) LIKE '%return%' "
        " AND lower(oracle_text) LIKE '%battlefield%' AND lower(oracle_text) LIKE '%under your control%')",
    ),

    "enters_battlefield": _or(
        "lower(oracle_text) LIKE '%when%enters%'",
        "lower(oracle_text) LIKE '%triggers an additional time%'",
    ),

    # ── Death / dies triggers ─────────────────────────────────────────────────
    # Commander fires when creatures die.
    # Producers: creatures with dies abilities + sacrifice outlets + token generators.
    "death_trigger": _or(
        "(lower(type_line) LIKE '%creature%' AND lower(oracle_text) LIKE '%when%dies%')",
        "lower(oracle_text) LIKE '%sacrifice a creature%'",
        "lower(oracle_text) LIKE '%sacrifice another creature%'",
        "lower(oracle_text) LIKE '%sacrifice%creature%'",
        # Token generators supply fodder
        "lower(oracle_text) LIKE '%create%token%'",
    ),

    # XMage alias
    "dies": _or(
        "(lower(type_line) LIKE '%creature%' AND lower(oracle_text) LIKE '%when%dies%')",
        "lower(oracle_text) LIKE '%sacrifice%creature%'",
        "lower(oracle_text) LIKE '%create%token%'",
    ),

    # ── Graveyard ─────────────────────────────────────────────────────────────
    "graveyard_from_play": _or(
        "lower(oracle_text) LIKE '%sacrifice%'",
        "(lower(type_line) LIKE '%creature%' AND lower(oracle_text) LIKE '%when%dies%')",
        "lower(oracle_text) LIKE '%mill%'",
    ),

    "graveyard_payoff": _or(
        "lower(oracle_text) LIKE '%return%graveyard%battlefield%'",
        "lower(oracle_text) LIKE '%creature card from%graveyard%battlefield%'",
        "lower(oracle_text) LIKE '%unearth%'",
        "lower(oracle_text) LIKE '%escape%'",
        "lower(oracle_text) LIKE '%flashback%'",
        "lower(oracle_text) LIKE '%you may cast%from your graveyard%'",
        "lower(oracle_text) LIKE '%activate%only from%graveyard%'",
        "lower(oracle_text) LIKE '%mill%'",
    ),

    # ── Attack triggers ───────────────────────────────────────────────────────
    # Commander fires when creatures attack.
    # Producers: creatures with attack triggers + haste enablers.
    "attack_trigger": _or(
        "(lower(type_line) LIKE '%creature%' AND lower(oracle_text) LIKE '%whenever%attacks%')",
        "'Haste' = ANY(keywords)",
        "lower(oracle_text) LIKE '%haste%'",
        # Double-strike and menace reward attacking
        "'Double Strike' = ANY(keywords)",
        "'Menace' = ANY(keywords)",
    ),

    # XMage alias
    "attacks": _or(
        "(lower(type_line) LIKE '%creature%' AND lower(oracle_text) LIKE '%whenever%attacks%')",
        "'Haste' = ANY(keywords)",
        "lower(oracle_text) LIKE '%haste%'",
        "'Menace' = ANY(keywords)",
    ),

    # ── Combat damage to player ───────────────────────────────────────────────
    # Commander fires when a creature deals combat damage to a player.
    # Producers: evasive creatures that reliably connect.
    "combat_damage_to_player": _or(
        "'Flying' = ANY(keywords)",
        "'Shadow' = ANY(keywords)",
        "'Horsemanship' = ANY(keywords)",
        "'Menace' = ANY(keywords)",
        "'Trample' = ANY(keywords)",
        "lower(oracle_text) LIKE '%can''t be blocked%'",
        "lower(oracle_text) LIKE '%unblockable%'",
        # Creatures that reward dealing combat damage (chain-synergy)
        "(lower(type_line) LIKE '%creature%' AND lower(oracle_text) LIKE '%deals combat damage%')",
    ),

    # XMage alias
    "combat_damage": _or(
        "'Flying' = ANY(keywords)",
        "'Shadow' = ANY(keywords)",
        "'Menace' = ANY(keywords)",
        "'Trample' = ANY(keywords)",
        "lower(oracle_text) LIKE '%can''t be blocked%'",
        "(lower(type_line) LIKE '%creature%' AND lower(oracle_text) LIKE '%deals combat damage%')",
    ),

    # ── Spell cast: creatures ─────────────────────────────────────────────────
    # Commander fires when you cast a creature spell.
    # Producers: creature spells (the cast triggers the commander).
    "cast_trigger_creature": (
        "lower(type_line) LIKE '%creature%'"
        " AND lower(type_line) NOT LIKE '%land%'"
    ),

    # XMage alias
    "creature_cast": (
        "lower(type_line) LIKE '%creature%'"
        " AND lower(type_line) NOT LIKE '%land%'"
    ),

    # ── Spell cast: instants / sorceries ──────────────────────────────────────
    "cast_trigger_instant_sorcery": _or(
        "lower(type_line) LIKE '%instant%'",
        "lower(type_line) LIKE '%sorcery%'",
    ),

    # XMage aliases
    "instant_sorcery_cast": _or(
        "lower(type_line) LIKE '%instant%'",
        "lower(type_line) LIKE '%sorcery%'",
    ),

    "spell_cast": _or(
        "lower(type_line) LIKE '%instant%'",
        "lower(type_line) LIKE '%sorcery%'",
        "lower(type_line) LIKE '%creature%'",
    ),

    "noncreature_cast": _or(
        "lower(type_line) LIKE '%instant%'",
        "lower(type_line) LIKE '%sorcery%'",
        "lower(type_line) LIKE '%enchantment%'",
        "lower(type_line) LIKE '%artifact%'",
    ),

    "spirit_arcane_cast": _or(
        "(lower(type_line) LIKE '%creature%' AND lower(type_line) LIKE '%spirit%')",
        "lower(oracle_text) LIKE '%arcane%'",
    ),

    # ── Spell cast: enchantments ──────────────────────────────────────────────
    "cast_trigger_enchantment": "lower(type_line) LIKE '%enchantment%'",

    "enchantment_cast": "lower(type_line) LIKE '%enchantment%'",

    # ── Spell cast: artifacts ─────────────────────────────────────────────────
    "cast_trigger_artifact": "lower(type_line) LIKE '%artifact%'",

    "artifact_cast": "lower(type_line) LIKE '%artifact%'",

    # ── Spell cast: historic (legendary + artifact + saga) ────────────────────
    "cast_trigger_historic": _or(
        "lower(type_line) LIKE '%legendary%'",
        "lower(type_line) LIKE '%artifact%'",
        "(lower(type_line) LIKE '%enchantment%' AND lower(type_line) LIKE '%saga%')",
    ),

    "historic_cast": _or(
        "lower(type_line) LIKE '%legendary%'",
        "lower(type_line) LIKE '%artifact%'",
        "(lower(type_line) LIKE '%enchantment%' AND lower(type_line) LIKE '%saga%')",
    ),

    # ── Spell cast: colored ───────────────────────────────────────────────────
    # Commander fires when you cast a spell of a specific color.
    # Producers: any non-land spell (color identity filter is applied per commander).
    "cast_trigger_colored": (
        "lower(type_line) NOT LIKE '%land%'"
        " AND (lower(type_line) LIKE '%instant%' OR lower(type_line) LIKE '%sorcery%'"
        "      OR lower(type_line) LIKE '%creature%' OR lower(type_line) LIKE '%enchantment%'"
        "      OR lower(type_line) LIKE '%artifact%')"
    ),

    # ── Sacrifice payoff ──────────────────────────────────────────────────────
    # Commander rewards sacrificing permanents.
    # Producers: sacrifice outlets + token generators (fodder).
    "sacrifice_payoff": _or(
        "lower(oracle_text) LIKE '%sacrifice%creature%'",
        "lower(oracle_text) LIKE '%sacrifice a permanent%'",
        "lower(oracle_text) LIKE '%sacrifice another%'",
        # Token generators supply fodder
        "lower(oracle_text) LIKE '%create%token%'",
        "lower(oracle_text) LIKE '%creates%token%'",
    ),

    # XMage alias
    "sacrifice": _or(
        "lower(oracle_text) LIKE '%sacrifice%creature%'",
        "lower(oracle_text) LIKE '%sacrifice a permanent%'",
        "lower(oracle_text) LIKE '%create%token%'",
    ),

    # ── Discard outlet ────────────────────────────────────────────────────────
    # Commander has a discard outlet; deck wants cards that benefit from being
    # discarded (madness, cycling) or that enable discard loops.
    "discard_outlet": _or(
        "'Madness' = ANY(keywords)",
        "lower(oracle_text) LIKE '%madness%'",
        "'Cycling' = ANY(keywords)",
        "lower(oracle_text) LIKE '%cycling%'",
        "lower(oracle_text) LIKE '%discard%draw%'",
        "lower(oracle_text) LIKE '%draw%discard%'",
        "lower(oracle_text) LIKE '%loot%'",
    ),

    # XMage alias
    "discard": _or(
        "'Madness' = ANY(keywords)",
        "lower(oracle_text) LIKE '%madness%'",
        "'Cycling' = ANY(keywords)",
        "lower(oracle_text) LIKE '%cycling%'",
        "lower(oracle_text) LIKE '%discard%draw%'",
    ),

    # ── Madness payoff ────────────────────────────────────────────────────────
    # Commander rewards discarding; deck wants Madness cards specifically.
    "madness_payoff": _or(
        "'Madness' = ANY(keywords)",
        "lower(oracle_text) LIKE '%for its madness cost%'",
        # Discard outlets so Madness cards can be cast
        "lower(oracle_text) LIKE '%discard%draw%'",
        "lower(oracle_text) LIKE '%draw%discard%'",
    ),

    # ── Landfall ──────────────────────────────────────────────────────────────
    # Commander fires when lands enter.
    # Producers: land-search effects + extra land drops + lands themselves.
    "landfall": _or(
        # Land tutors / ramp
        "lower(oracle_text) LIKE '%search your library for%land%put it onto the battlefield%'",
        "lower(oracle_text) LIKE '%search your library for%land%put that card onto the battlefield%'",
        "lower(oracle_text) LIKE '%put%land%onto the battlefield%'",
        "lower(oracle_text) LIKE '%you may put%land%into play%'",
        # Extra land drops
        "lower(oracle_text) LIKE '%play an additional land%'",
        "lower(oracle_text) LIKE '%you may play%additional land%'",
        "lower(oracle_text) LIKE '%two lands per turn%'",
        # Lands themselves (fetchlands, etc.)
        "lower(type_line) LIKE '%land%'",
    ),

    # XMage alias
    "landfall_xmage": _or(
        "lower(oracle_text) LIKE '%search your library for%land%onto the battlefield%'",
        "lower(oracle_text) LIKE '%play an additional land%'",
        "lower(type_line) LIKE '%land%'",
    ),

    # ── +1/+1 counter placement ───────────────────────────────────────────────
    # Commander fires when counters are placed.
    # Producers: counter placement effects.
    "counter_placement": _or(
        "lower(oracle_text) LIKE '%put a +1/+1 counter%'",
        "lower(oracle_text) LIKE '%put two +1/+1 counters%'",
        "lower(oracle_text) LIKE '%put x +1/+1 counters%'",
        "lower(oracle_text) LIKE '%+1/+1 counter on each%'",
        "lower(oracle_text) LIKE '%proliferate%'",
    ),

    # XMage alias
    "counter_added": _or(
        "lower(oracle_text) LIKE '%put a +1/+1 counter%'",
        "lower(oracle_text) LIKE '%put two +1/+1 counters%'",
        "lower(oracle_text) LIKE '%proliferate%'",
    ),

    # ── Counter doubling ──────────────────────────────────────────────────────
    "counter_doubler": _or(
        "lower(oracle_text) LIKE '%double the number of counters%'",
        "lower(oracle_text) LIKE '%twice the number of%counter%'",
        "lower(oracle_text) LIKE '%one additional +1/+1 counter%'",
        "lower(oracle_text) LIKE '%an additional +1/+1 counter%'",
        "lower(oracle_text) LIKE '%proliferate%'",
        "lower(oracle_text) LIKE '%put a +1/+1 counter%'",
    ),

    # ── Proliferate / poison counters ─────────────────────────────────────────
    "proliferate_matters": _or(
        "lower(oracle_text) LIKE '%proliferate%'",
        "lower(oracle_text) LIKE '%infect%'",
        "lower(oracle_text) LIKE '%toxic%'",
        "lower(oracle_text) LIKE '%wither%'",
        "lower(oracle_text) LIKE '%poison counter%'",
        "lower(oracle_text) LIKE '%-1/-1 counter%'",
        "lower(type_line) LIKE '%planeswalker%'",
        "lower(oracle_text) LIKE '%loyalty counter%'",
    ),

    # XMage alias (adapt/evolve get counter growth from proliferate / placement)
    "adapt_evolve": _or(
        "lower(oracle_text) LIKE '%put a +1/+1 counter%'",
        "lower(oracle_text) LIKE '%put two +1/+1 counters%'",
        "lower(oracle_text) LIKE '%put x +1/+1 counters%'",
        "lower(oracle_text) LIKE '%+1/+1 counter on each%'",
        "lower(oracle_text) LIKE '%proliferate%'",
        "lower(oracle_text) LIKE '%double the number of counters%'",
        "lower(oracle_text) LIKE '%one additional +1/+1 counter%'",
        "lower(oracle_text) LIKE '%an additional +1/+1 counter%'",
    ),

    # ── Lifegain trigger ──────────────────────────────────────────────────────
    # Commander fires when you gain life.
    # Producers: lifelink creatures + lifegain effects.
    "lifegain_trigger": _or(
        "'Lifelink' = ANY(keywords)",
        "lower(oracle_text) LIKE '%you gain%life%'",
        "lower(oracle_text) LIKE '%gain%life%for each%'",
        "lower(oracle_text) LIKE '%lifelink%'",
    ),

    # XMage alias
    "lifegain": _or(
        "'Lifelink' = ANY(keywords)",
        "lower(oracle_text) LIKE '%you gain%life%'",
        "lower(oracle_text) LIKE '%lifelink%'",
    ),

    # ── Draw trigger ──────────────────────────────────────────────────────────
    # Commander fires when you draw cards.
    # Producers: card draw effects.
    "draw_trigger": (
        "lower(oracle_text) LIKE '%draw%card%'"
        " AND lower(type_line) NOT LIKE '%land%'"
    ),

    # XMage alias
    "spell_draw": (
        "lower(oracle_text) LIKE '%draw%card%'"
        " AND lower(type_line) NOT LIKE '%land%'"
    ),

    # ── Token creation trigger ────────────────────────────────────────────────
    "token_trigger": _or(
        "lower(oracle_text) LIKE '%create%token%'",
        "lower(oracle_text) LIKE '%creates%token%'",
        "lower(oracle_text) LIKE '%put%token%'",
    ),

    # ── Trigger doubling ──────────────────────────────────────────────────────
    # Commander doubles triggers.
    # Producers: creatures with ETBs (the doubled abilities) + other doublers.
    "trigger_doubling": _or(
        "lower(oracle_text) LIKE '%triggers an additional time%'",
        "lower(oracle_text) LIKE '%triggered abilit%'",
        "(lower(type_line) LIKE '%creature%' AND lower(oracle_text) LIKE '%when%enters%')",
        "(lower(type_line) LIKE '%creature%' AND lower(oracle_text) LIKE '%when%dies%')",
        "(lower(type_line) LIKE '%creature%' AND lower(oracle_text) LIKE '%whenever%attacks%')",
    ),

    # ── Keyword lord ──────────────────────────────────────────────────────────
    # Commander grants a keyword to creatures you control.
    # Producers: creatures that benefit most from having that keyword.
    # (Since we don't know which keyword is being granted, select creatures
    # with high-value keywords that are often the grant target.)
    "keyword_lord": _or(
        "'Flying' = ANY(keywords)",
        "'Trample' = ANY(keywords)",
        "'Haste' = ANY(keywords)",
        "'Menace' = ANY(keywords)",
        "'Deathtouch' = ANY(keywords)",
        "'Lifelink' = ANY(keywords)",
        "'Hexproof' = ANY(keywords)",
        "'Indestructible' = ANY(keywords)",
        "'First Strike' = ANY(keywords)",
        "'Double Strike' = ANY(keywords)",
        "'Vigilance' = ANY(keywords)",
    ),

    # ── Cycling trigger ───────────────────────────────────────────────────────
    "cycling_trigger": _or(
        "'Cycling' = ANY(keywords)",
        "lower(oracle_text) LIKE '%cycling {%'",
        "lower(oracle_text) LIKE '%cycling—%'",
        "lower(oracle_text) LIKE '%you may cycle%'",
    ),

    # ── Second spell matters ──────────────────────────────────────────────────
    # Commander rewards casting multiple spells per turn.
    # Producers: low-cost instants/sorceries (easy to cast as second spell).
    "second_spell": (
        "(lower(type_line) LIKE '%instant%' OR lower(type_line) LIKE '%sorcery%')"
        " AND cmc <= 3"
    ),

    # ── Punisher ──────────────────────────────────────────────────────────────
    # Commander pings opponents for game events.
    # Producers: effects that drain each opponent or the table.
    "punisher": _or(
        "lower(oracle_text) LIKE '%each opponent loses%life%'",
        "lower(oracle_text) LIKE '%deals%damage to each opponent%'",
        "lower(oracle_text) LIKE '%each player loses%life%'",
        "lower(oracle_text) LIKE '%each player%loses%life%'",
        "lower(oracle_text) LIKE '%whenever%opponent%draws%damage%'",
    ),

    # ── Weenie matters ────────────────────────────────────────────────────────
    # Commander cares about low-power creatures.
    # Producers: efficient low-CMC creatures.
    "weenie_matters": (
        "lower(type_line) LIKE '%creature%'"
        " AND cmc <= 2"
        " AND lower(type_line) NOT LIKE '%land%'"
    ),

    # ── Extra combat ──────────────────────────────────────────────────────────
    # Commander grants or rewards extra combat phases.
    # Producers: extra combat spells + haste enablers (so creatures can attack again).
    "extra_combat": _or(
        "lower(oracle_text) LIKE '%additional combat phase%'",
        "lower(oracle_text) LIKE '%second combat phase%'",
        "lower(oracle_text) LIKE '%there is an additional combat%'",
        "'Haste' = ANY(keywords)",
        "lower(oracle_text) LIKE '%haste%'",
        # Double-strike benefits from multiple combats
        "'Double Strike' = ANY(keywords)",
    ),

    # ── Equipment matters ─────────────────────────────────────────────────────
    "equipment_matters": _or(
        "lower(type_line) LIKE '%equipment%'",
        "lower(oracle_text) LIKE '%equip {0}%'",
        "lower(oracle_text) LIKE '%equip costs%less%'",
        "lower(oracle_text) LIKE '%equip abilities%less%'",
        "lower(oracle_text) LIKE '%enters the battlefield attached%'",
        "lower(oracle_text) LIKE '%attach target equipment%'",
        "lower(oracle_text) LIKE '%attach it to target creature%'",
    ),

    # ── Stax: opponent restriction ────────────────────────────────────────────
    # Commander restricts what opponents can do.
    # Producers: more cards that lock opponents' spells / abilities.
    "opponent_restriction": _or(
        "lower(oracle_text) LIKE '%opponents can''t%'",
        "lower(oracle_text) LIKE '%your opponents can''t%'",
        "lower(oracle_text) LIKE '%each opponent can''t%'",
        "lower(oracle_text) LIKE '%players can''t cast%'",
        "lower(oracle_text) LIKE '%can''t cast spells%'",
        "lower(oracle_text) LIKE '%can''t draw more than%'",
    ),

    # ── Stax: activated ability restriction ───────────────────────────────────
    "activated_restriction": _or(
        "lower(oracle_text) LIKE '%activated abilit%can''t be activated%'",
        "lower(oracle_text) LIKE '%activated abilit%of creatures%can''t%'",
        "lower(oracle_text) LIKE '%activated abilit%your opponents%can''t%'",
    ),

    # ── Stax: tax effect ──────────────────────────────────────────────────────
    "tax_effect": _or(
        "lower(oracle_text) LIKE '%cost%{1} more%'",
        "lower(oracle_text) LIKE '%cost%{2} more%'",
        "lower(oracle_text) LIKE '%costs {1} more%'",
        "lower(oracle_text) LIKE '%costs {2} more%'",
        "lower(oracle_text) LIKE '%costs more to cast%'",
        "lower(oracle_text) LIKE '%spells%opponents%cost%more%'",
    ),

    # ── Stax: opponents' permanents enter tapped ──────────────────────────────
    "enters_tapped_opponent": _or(
        "(lower(oracle_text) LIKE '%enter%tapped%' AND lower(oracle_text) LIKE '%opponent%')",
        "(lower(oracle_text) LIKE '%enter%tapped%' AND lower(oracle_text) LIKE '%players%')",
        "lower(oracle_text) LIKE '%nonbasic land%enters tapped%'",
        "lower(oracle_text) LIKE '%creatures%enter tapped%'",
    ),

    # ── Monarch ───────────────────────────────────────────────────────────────
    # Commander interacts with the monarch mechanic.
    # Producers: cards that get or use the monarch crown + combat creatures.
    "monarch": _or(
        "lower(oracle_text) LIKE '%monarch%'",
        # Evasive creatures to defend / regain the crown in combat
        "'Flying' = ANY(keywords)",
        "lower(oracle_text) LIKE '%can''t be blocked%'",
        # Combat damage to player payoffs chain well with monarch
        "(lower(type_line) LIKE '%creature%' AND lower(oracle_text) LIKE '%deals combat damage%')",
    ),

    # ── Initiative ────────────────────────────────────────────────────────────
    # Commander interacts with the initiative mechanic.
    # Producers: dungeon-advance effects + efficient attackers to hold initiative.
    "initiative": _or(
        "lower(oracle_text) LIKE '%initiative%'",
        "lower(oracle_text) LIKE '%dungeon%'",
        "lower(oracle_text) LIKE '%venture into the dungeon%'",
        # Attackers to re-take initiative after losing it
        "'Haste' = ANY(keywords)",
        "'Flying' = ANY(keywords)",
    ),

    # ── Goad ─────────────────────────────────────────────────────────────────
    # Commander goads opponents' creatures.
    # Producers: more goad effects + evasion for your own creatures (to attack freely).
    "goad": _or(
        "lower(oracle_text) LIKE '%goad%'",
        "lower(oracle_text) LIKE '%attacks each combat if able%'",
        # Your creatures need to be safe while opponents fight each other
        "'Indestructible' = ANY(keywords)",
        "'Flying' = ANY(keywords)",
        "'Menace' = ANY(keywords)",
        "lower(oracle_text) LIKE '%can''t be blocked%'",
    ),

    # ── Forced attack ─────────────────────────────────────────────────────────
    # Commander forces all/certain creatures to attack.
    # Producers: haste enablers + protection for forced attackers + attack rewards.
    "forced_attack": _or(
        "lower(oracle_text) LIKE '%attacks each combat if able%'",
        "'Haste' = ANY(keywords)",
        "lower(oracle_text) LIKE '%haste%'",
        "'Indestructible' = ANY(keywords)",
        "lower(oracle_text) LIKE '%indestructible%'",
        "'Trample' = ANY(keywords)",
    ),

    # ── Poison / infect / toxic ───────────────────────────────────────────────
    # Commander interacts with the poison counter win condition.
    # Producers: infect/toxic creatures + proliferate.
    "poison_infect": _or(
        "lower(oracle_text) LIKE '%infect%'",
        "lower(oracle_text) LIKE '%toxic%'",
        "lower(oracle_text) LIKE '%poison counter%'",
        "lower(oracle_text) LIKE '%proliferate%'",
    ),

    # ── Group hug ─────────────────────────────────────────────────────────────
    # Commander gives resources to all players.
    # Producers: more group-draw / group-mana effects.
    "group_hug": _or(
        "lower(oracle_text) LIKE '%each player draws%'",
        "lower(oracle_text) LIKE '%each player may draw%'",
        "lower(oracle_text) LIKE '%each player%draws a card%'",
        "lower(oracle_text) LIKE '%each player%draw%card%'",
        "lower(oracle_text) LIKE '%all players draw%'",
        "lower(oracle_text) LIKE '%each player%additional%mana%'",
        "lower(oracle_text) LIKE '%each player%may put%land%'",
    ),
}
