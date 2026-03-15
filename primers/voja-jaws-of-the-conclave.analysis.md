# Voja, Jaws of the Conclave — Primer Analysis

**Commander:** Voja, Jaws of the Conclave {R}{G}{W} — 3/3 Legendary Wolf, Trample
**Color identity:** Naya (RGW)
**Source:** Moxfield primer by Hydrax (last updated 2026-02-11)
**Theme:** Elf tribal combat pump + Wolf synergy; Elf mana engine; draw per Elf/Wolf on attack; Bracket 4

---

## Commander Ability (the trigger)

> Whenever Voja, Jaws of the Conclave attacks, put a +1/+1 counter on each Wolf and each Elf you control, then draw a card for each Wolf you control.
> (Voja himself is a Wolf; Wolves count for the draw trigger)

Key constraints:
- **All Elves grow every attack** — the deck has 20+ Elves, creating massive board-wide buff per attack
- **Draw = number of Wolves** — incentivizes having Wolf tokens or actual Wolves in play
- **Elves AND Wolves are both supported** — Elves for mana engine + combat pump; Wolves for card draw
- **Trample**: Voja himself has trample; with +1/+1 counter stacking, he becomes a lethal commander-damage threat

---

## Primary Synergy Packages (by the primer author's own labels)

### 1. Elf mana engine (29 ramp pieces)
An unusual number of ramp pieces for a "combat" deck — Elves ARE the ramp:
- **1-mana dorks**: Llanowar Elves, Fyndhorn Elves, Elvish Mystic, Boreal Druid, Birds of Paradise
- **Scaling dorks**: Priest of Titania (mana per Elf), Elvish Archdruid (mana per Elf, also buffs all Elves), Circle of Dreams Druid, Marwyn the Nurturer (mana = power, grows with Voja attacks), Selvala Heart of the Wilds (mana = highest power in play)
- **Land ramp Elves**: Wood Elves, Nature's Lore, Three Visits, Farseek, Archdruid's Charm

**Model insight:** Elfball mana engines are captured by `compute_tribal_typeline_synergy()` but the specific interaction between "Elf" type + mana tap abilities is more nuanced. Selvala and Marwyn scale based on creature stats, not just creature count.

### 2. Wolf token generation (for card draw)
More Wolves = more cards drawn with each Voja attack. Wolf generators:
- **Tolsimir, Midnight's Light**: Creates Wolf tokens on creature ETBs (flavor connection to Voja's lore)
- **Mirror Entity, Maskwood Nexus**: Makes every creature every type — all Elves become Wolves, dramatically increasing draw
- **Annie Joins Up**: Doubles Voja's trigger (both +1/+1 counters AND card draw trigger twice)
- **Deeproot Pilgrimage / Deeproot Waters**: Merfolk token generators (off-theme but create tokens for doubling)

**Model insight:** "Changeling all creature types" (Maskwood Nexus, Mirror Entity) interacts with tribal draw commanders in a unique way — they convert every creature into the draw-relevant type.

### 3. Combat protection + pump
With so many Elves, protection of the board is critical:
- **Instant-speed board protection**: Heroic Intervention, Inspiring Call, Teferi's Protection, Flawless Maneuver, Deflecting Swat, Mithril Coat
- **Evasion enablers**: Bramblewood Paragon (trample to Elves + Warriors), Finale of Devastation (haste at X = large)
- **Lightning Greaves**: Haste + shroud for Voja immediately on entry

### 4. Combo package (late-game finisher)
Two combo lines exist as game-closers when combat damage is insufficient:

**Combo 1: Staff of Domination infinite mana + Finale of Devastation**
- 5-mana dork → tap for 5 → {3} untap dork, {1} untap Staff → net 1 mana per cycle
- Infinite mana → draw entire deck via Staff → cast Finale of Devastation for lethal → attack with haste

**Combo 2: The Red Terror + All Will Be One**
- Counter placed on any creature (via Voja attack) → AWBO deals 1 damage → Red Terror sees non-counter source trigger → puts counter on itself → AWBO triggers again → infinite damage

---

## Combo Lines

### Line 1: Staff of Domination (infinite mana → draw → combat win)
- Requirements: Any dork that nets ≥ 5 mana (Gyre Sage, Elvish Archdruid, Priest of Titania, Marwyn, Circle of Dreams Druid, or Selvala), Staff of Domination untapped
- Loop: Tap dork for ≥ 5 → {3} untap dork → {1} untap Staff → net ≥ 1 mana → repeat
- Then: Use {5} draw + {1} untap for infinite card draw → find Finale of Devastation + Ezuri → cast Finale for lethal + haste
- Tutors: Enlightened Tutor (for Staff), Fauna Shaman, Archdruid's Charm, Rocco Cabaretti Caterer (creature tutors)

### Line 2: The Red Terror + All Will Be One + counter source
- Requirements: Red Terror on field, AWBO on field, any trigger that places a +1/+1 counter
- Start: Voja attacks → +1/+1 counter on all Elves/Wolves → AWBO fires → Red Terror triggers → counter on Red Terror → AWBO fires again → repeat
- Win: Infinite damage to all opponents simultaneously

---

## Deckbuilding Principles Stated by Author

1. **Turbo out Voja ASAP** — Even Voja alone draws 1 card per attack (he's a Wolf); early Voja is better than waiting for the perfect board.
2. **Don't overcommit into board wipes** — Elf density makes recovery difficult; protect the board or play conservatively.
3. **Track commander damage** — Voja grows every attack and has Trample; 21 commander damage is achievable mid-game.
4. **Elves ARE the ramp** — Unlike typical Naya commanders, Elves serve dual-duty as mana sources AND combat threats.
5. **Annie Joins Up > Roaming Throne** — Annie doubles Voja's trigger AND is harder to remove (not a creature); preferred over traditional tribal doubler.

---

## Synergy Gaps vs. Current Pipeline

| Gap | Description | Example cards |
|---|---|---|
| Wolf tribal | Wolves are a supported creature type for Voja's draw trigger; not currently in TRIBES list | Tolsimir Midnight's Light, Voja (Wolf count → card draw); Wolf tokens |
| Draw-per-creature-type-count | Voja draws cards equal to number of a specific type (Wolves); scales with tribal density | Voja, Jaws of the Conclave (consumer of Wolf count) |
| Changeling-as-type-expansion | Mirror Entity / Maskwood Nexus converts all creatures to all types, multiplying tribal-count payoffs | Maskwood Nexus, Mirror Entity (producers of all-type benefit) |
| Staff of Domination infinite mana loop | Netting mana with Staff via creature untap → Staff draw → infinite; specific artifact combo | Staff of Domination (combo hub for multiple mana dork loops) |
| Scaling mana dorks | Dorks whose mana production scales with creature stats (Selvala, Marwyn) are more valuable than flat producers in late game | Selvala Heart of the Wilds, Marwyn the Nurturer |
