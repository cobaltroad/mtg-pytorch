# Magus Lucea Kane — Primer Analysis

**Commander:** Magus Lucea Kane {1}{G}{U}{R} — Temur, Legendary Human Mutant
**Color identity:** Temur (GUR)
**Source:** Moxfield primer by Hydrax (last updated 2026-01-28)
**Theme:** X-spell doubling via tapping MLK for mana + copying; Tyranid tribal; stompy +1/+1 counters; casual/high-power casual

---

## Commander Ability (the trigger)

> {T}: Add {C}{C}. When you cast your next instant or sorcery spell with X in its mana cost this turn, copy it. You may choose new targets for the copy.
> (This is a mana ability — cannot be responded to; the delayed copy trigger CAN be responded to.)

Key constraints:
- The **mana ability** cannot be countered, but the copy trigger can be (e.g. Disallow)
- **Multiple taps = multiple copies**: Untapping MLK (Kiora's Follower, Formidable Speaker) creates additional delayed triggers — next X-spell gets copied multiple times
- The copy **retains the X value** — a Mawloc for X=5 creates a copy with X=5
- Best value: withhold MLK from the field until you have an X-spell ready

---

## Primary Synergy Packages (by the primer author's own labels)

### 1. X-spell density + copy multipliers
The deck is built around high-value X-spells that benefit massively from being copied:
- **Tyranid X-creatures** (Ravenous mechanic — draw if X ≥ 5): Hormagaunt Horde, Termagant Swarm, Aberrant, Mawloc, Tervigon, Exocrine, Tyrant Guard, Broodlord
- **Non-Tyranid X-spells**: Hydroid Krasis (half life gain + half card draw), Animist's Awakening, Open the Way, Shivan Devastator, Primordial Hydra, Genesis Hydra
- **Copy enablers**: Tap MLK multiple times via untappers → each untap = additional copy of next X-spell

**Model insight:** X-spell copying is a distinct producer→consumer pattern: X-spell casters produce tokens/counters/draw, and MLK is the multiplier. The model needs to learn that cards with variable X cost are more valuable alongside copy effects.

### 2. MLK untap engine
Untapping MLK before casting an X-spell multiplies copies:
- **Kiora's Follower**: Tap to untap any permanent (including MLK) — nets mana + extra copy trigger
- **Formidable Speaker**: Similar untap effect on any creature
- **Kiora, Behemoth Beckoner**: Untaps a permanent when a creature with power 4+ ETBs

**Model insight:** Cards that untap a specific creature (or any creature) have elevated value as "copy amplifiers" for MLK. This is not captured by standard synergy edges.

### 3. Tyranid tribal + +1/+1 counters
Tyranid creatures (from Warhammer 40k crossover) often have Ravenous — draw a card if X ≥ 5 when cast:
- **Counter swarm**: Biophagus (counter doubler), Kami of Whispered Hopes, Benevolent Hydra, Vorinclex Monstrous Raider, Duskshell Crawler
- **Evasion payoffs**: Herald of Secret Streams (unblockable with counters), Winged Hive Tyrant, Court of Garenbrig
- **Counter-rich creatures**: Most Tyranid Ravenous creatures ETB with X counters

### 4. Big mana / stompy ramp
Getting X-spells to their meaningful values requires massive mana:
- **Dorks**: Elvish Mystic, Fyndhorn Elves, Llanowar Elves, Birds of Paradise, Selvala Heart of the Wilds, Incubation Druid
- **Enchantments**: Garruk's Uprising, Bred for the Hunt (draw when +1/+1 creature attacks)
- **Land ramp**: Animist's Awakening (X-spell + copied = 2X lands), Open the Way (copied = 2× lands per opponent)

---

## Combo Lines

No formal infinite combos in this build (author describes it as "casual / borderline high-power casual" with "big dumb creatures turning sideways"). The closest to infinite:

### Near-combo: MLK × Kiora's Follower × Formidable Speaker
- Tap MLK ({C}{C}), tap Kiora's Follower to untap MLK, tap MLK again ({C}{C}), tap Formidable Speaker to untap MLK, repeat
- Each untap-retap costs 1 creature tap but generates 2 more mana and 1 additional copy trigger
- If mana dorks produce enough mana to sustain this loop, you can create many copies of one X-spell

---

## Deckbuilding Principles Stated by Author

1. **Wait to deploy MLK until you have an X-spell ready** — She becomes a removal target; don't let her sit idle.
2. **Tyranid Timmy philosophy** — Flavor over raw competitive power; big creatures that grow endlessly.
3. **Open the Way copied at X=4 (4-player game) = 8 lands** — Land ramp X-spells become absurd when doubled.
4. **Upgrade path**: More protection spells + Thousand-Year Elixir for untapping MLK repeatedly.
5. **Green is the primary color** — Mana base should ensure green is always accessible.

---

## Synergy Gaps vs. Current Pipeline

| Gap | Description | Example cards |
|---|---|---|
| X-spell / variable-cost synergy | Cards with X in mana cost have special value when copy effects exist; not captured as a synergy category | Hydroid Krasis, Mawloc, Shivan Devastator, Primordial Hydra (producers); MLK (multiplier) |
| Ravenous mechanic | Tyranid mechanic: draw a card when you cast this for X ≥ 5; not in TRIGGER_PATTERNS | Mawloc, Tyrant Guard, Exocrine, Tervigon (all Tyranid Ravenous creatures) |
| Creature untap as copy amplifier | Untapping a creature that has a tap-for-copy-ability creates multiple copies; niche pattern | Kiora's Follower, Formidable Speaker (enablers of MLK copy doubling) |
| Stompy power threshold | Cards like Selvala, Garruk's Uprising trigger on power ≥ 4+; distinct from generic ETB triggers | Selvala Heart of the Wilds, Garruk's Uprising, Kiora Behemoth Beckoner |
| Tyranid tribal | Tyranid is a creature type from the Warhammer 40k crossover; not in standard TRIBES list | All Tyranid creatures from the Warhammer Commander product |
