# Atraxa, Praetors' Voice — Primer Analysis

**Commander:** Atraxa, Praetors' Voice {G}{W}{U}{B} — 4/4 Legendary Phyrexian Angel Horror, Flying, Vigilance, Deathtouch, Lifelink
**Color identity:** GWUB (no red)
**Source:** Moxfield primer by Hydrax (updated 2024-07-16)
**Theme:** Poison/infect aggro; apply one poison counter to every opponent early, then proliferate to 10; Atraxa is an amplifier not a requirement — deck functions without her in play

---

## Commander Ability (the trigger)

> At the beginning of your end step, proliferate.

Key constraints:
- **Proliferate fires once per turn automatically** — it is a time-based end-step trigger, not paired with any spell, combat action, or other card; it activates even if Atraxa does nothing else that turn
- **Proliferate requires existing counters** — if no opponent has a poison counter yet, Atraxa's end-step trigger does nothing useful; the deck must land poison first
- **Commander-independent gameplan** — the deck runs 14 other proliferate sources; Atraxa is a removal magnet and the author explicitly builds around losing her early
- **Toxic vs. Infect distinction** — Toxic applies counters equal to the Toxic value regardless of damage dealt through blockers; Infect applies counters equal to the creature's power; both require combat damage to a player

---

## Primary Synergy Packages

### 1. Poison counter delivery (infect and toxic creatures)
Getting the first poison counter on each opponent is the prerequisite for proliferate value:
- **Blighted Agent**: 1/1 unblockable Infect — easiest poison applicator in the format; opponents cannot stop it without instant-speed removal
- **Ichor Rats**: ETB gives ALL opponents one poison counter immediately — single card clears the "poison delivery" requirement for all three opponents
- **Plague Stinger / Swamp Mosquito / Pestilent Syphoner**: cheap flying Infect/Toxic creatures that get in for damage before opponents stabilize
- **Phyrexian Crusader**: Infect + protection from White and Red; bypasses the most common removal colors
- **Skithiryx, the Blight Dragon**: Flying, Infect, 4 power + haste activation; can apply 4 poison in a turn with no warning
- **Infectious Inquiry / Phyresis Outbreak / Vraska's Fall**: instant/sorcery catch-up pieces that give all opponents a poison counter without requiring combat
- **Bloated Contaminator**: 4/4 Trample + Toxic 1 for {3}; proliferates on combat damage — combines delivery and spreading in one card

### 2. Proliferate engine (non-Atraxa)
14 sources ensure the proliferate clock runs even when Atraxa is answered:
- **Inexorable Tide**: proliferate on every spell cast; fastest non-creature proliferate engine
- **Evolution Sage**: proliferate on every land drop; synergizes with the 4-color fetch-heavy mana base
- **Thrummingbird**: Flying + proliferate on combat damage; pairs with Atraxa's evasion package
- **Metastatic Evangel**: proliferate on every non-token creature ETB; rewards the high creature count
- **Viral Drake**: activated proliferate ({4}) — mana sink for turns with nothing to cast
- **Contagion Clasp**: artifact proliferate for {4}; resilient to creature removal
- **Karn's Bastion**: land that proliferates; uncounterable and unkillable outside of land removal
- **Ezuri, Stalker of Spheres**: draws a card per proliferate trigger; also ETB-proliferates twice for {5}{G}{U}
- **Vraska, Betrayal's Sting**: proliferates on her 0 ability (and gains loyalty doing so)

### 3. Evasion and combat support (pushing damage through)
Poison via combat requires unblocked hits:
- **Mother of Runes / Giver of Runes / Skrelv, Defector Mite**: tap to grant protection — lets infect creatures attack past blockers at instant speed; Skrelv also adds Toxic 1 to any creature
- **Champion of Lambholt**: grows a +1/+1 counter per creature played; proliferate pushes her further; eventually makes all your creatures unblockable
- **Flensing Raptor**: grants flying to another Toxic creature on ETB
- **Venerated Rotpriest**: whenever any of your creatures is targeted by a spell, an opponent gains a poison counter — turns targeted removal into free poison delivery

### 4. Card draw (sustaining through interaction)
- **Ezuri, Stalker of Spheres**: card per proliferate (synergy anchor)
- **Guardian Project**: draw on every non-token creature ETB in a 39-creature deck
- **Contaminant Grafter**: draw + land at end of turn if any opponent has 3+ poison; also a 5/5 Trample Toxic creature that proliferates on ANY creature's combat damage
- **Tamiyo, Field Researcher**: +1 makes two creatures draw cards on combat damage; ultimate gives permanent Omniscience; loyalty increases via proliferate

### 5. Planeswalker synergy
Planeswalkers accumulate loyalty counters; every proliferate trigger adds a counter to each planeswalker in play:
- **Tamiyo, Field Researcher**: reaches ultimate faster via proliferate; ultimate is permanent Omniscience
- **Vraska, Betrayal's Sting**: 0 ability draws + proliferates (self-reinforcing); -2 creates removal-via-Treasure; ultimate puts any player at 9 poison (one proliferate or Toxic hit away from 10)

---

## Combo Lines

### Line 1: Ichor Rats + any proliferate engine (spreading the contagion)
- Requirements: Ichor Rats on battlefield (or in hand), at least one proliferate source in play
- Loop: Cast Ichor Rats → all three opponents each receive 1 poison counter → begin proliferating each end step (Atraxa) or each spell (Inexorable Tide) or each land drop (Evolution Sage)
- Result: Poison counters increase on all opponents simultaneously each trigger; no further combat needed
- Win: Any opponent who reaches 10 poison loses the game regardless of life total

### Line 2: Food Chain + Eternal Scourge / Misthollow Griffin (not in this build — deck is combo-free per primer)
Note: This is an infect aggro build. The primer explicitly states "while it doesn't have combos, it can spread poison counters with brutal efficiency." There are no infinite combo lines — the win is through repeated proliferate accumulation.

### Line 3: Vraska, Betrayal's Sting ultimate + one proliferate
- Requirements: Vraska on field at ultimate loyalty, any proliferate effect available
- Loop: Activate Vraska's ultimate → target player goes to 9 poison counters → proliferate once (Atraxa end step, or any other engine) → that player reaches 10 and loses
- Result: Instant near-kill on one player, leaving two others to handle separately
- Win: Combine with Phyrexian Swarmlord (insects with Infect equal to total opponent poison) or Bloated Contaminator attacks to close out remaining opponents

### Line 4: Phyrexian Swarmlord late-game token generation
- Requirements: Phyrexian Swarmlord in play, opponents collectively have multiple poison counters
- Loop: Each upkeep, create N 1/1 Infect insects where N = total poison counters across all opponents; attack with insects
- Result: Scales exponentially with existing poison; a table with 4+4+4 = 12 total poison creates 12 Infect insects per turn
- Win: Champion of Lambholt makes insects unblockable; direct infect damage closes remaining poison gap

---

## Deckbuilding Principles Stated by Author

1. **Atraxa is an amplifier, not a crutch** — build the deck to win without her; treat every game where she sticks as a bonus, not a requirement.
2. **Start the clock before proliferating** — Atraxa's end-step proliferate does nothing until all opponents have at least one poison counter; prioritize delivery first, proliferation second.
3. **Mass delivery spells are highest priority** — Ichor Rats, Infectious Inquiry, Phyresis Outbreak, and Vraska's Fall give ALL opponents poison in one action; these are the deck's most efficient cards for enabling the proliferate engine.
4. **Evasion is mandatory** — the creature suite heavily favors flying and unblockable to ensure poison lands; ground creatures with Infect often get chump-blocked before doing damage.
5. **Do not cast Inexorable Tide until all opponents are infected** — it is a removal magnet; casting it before all opponents have poison wastes its value and exposes it to early removal.
6. **Hold back vs. over-extending** — if all three opponents are infected and the board is stable, proliferating and passing is often better than adding more creatures and risking a board wipe.
7. **4-color mana base must be green-anchored** — green ramp and mana fixing is the priority since early ramp mostly requires green; nearly every land either produces green or fetches a green source.

---

## Synergy Gaps vs. Current Pipeline

| Gap | Description | Example cards |
|---|---|---|
| Time-based proliferate trigger | Atraxa's proliferate fires at end of turn with no paired card; the pipeline looks for oracle-text pairs where one card enables another, but there is no "producing" card for Atraxa's trigger — it is purely time-based and cannot be captured as a synergy edge between card pairs | Atraxa (no consumer card text says "proliferate at end of step") |
| Poison counter as threshold condition | The pipeline cannot know that infect/toxic creatures want Atraxa specifically because proliferate advances poison counters toward 10; the connection between "this creature deals infect damage" and "a proliferate commander multiplies that damage" requires understanding the poison win condition | Blighted Agent, Phyrexian Crusader, Ichor Rats + Atraxa |
| Superfriends split vs. infect split | The same Atraxa commander produces two radically different card pools: (1) planeswalker-heavy "superfriends" decks using proliferate to advance loyalty counters, and (2) infect aggro decks like this one using proliferate to advance poison counters. The pipeline cannot distinguish which archetype a given deck belongs to, and would predict both planeswalkers and infect creatures as equally relevant | Tamiyo vs. Blighted Agent — correct for only one archetype |
| Loyalty counter proliferate value | Proliferate adds a loyalty counter to every planeswalker in play; the pipeline's regex synergy detection does not link proliferate effects to planeswalker loyalty mechanics because planeswalker loyalty is not an ability in oracle text — it is a game-state property | Tamiyo, Field Researcher; Vraska, Betrayal's Sting (both benefit from every proliferate trigger) |
| -1/-1 counter and charge counter recipients | Proliferate advances any counter type including -1/-1 counters (on opponent creatures), charge counters (on artifacts), and experience counters; the model has no way to know which counter types a deck is advancing without understanding the deck's strategy | Contagion Clasp (charge counter target); Phyresis Outbreak (-1/-1 synergy) |
| Mass poison delivery as combo enabler | Ichor Rats, Infectious Inquiry, and Phyresis Outbreak gain their value from functioning as "keys" that unlock the proliferate engine by giving all opponents a counter simultaneously; the pipeline sees their oracle text but cannot model the strategic importance of "infects all opponents at once" vs. "infects one opponent" | Ichor Rats vs. Blighted Agent — wildly different strategic roles despite both being infect cards |
| Venerated Rotpriest punishment loop | When opponents target your creatures with removal, Rotpriest gives them a poison counter; the pipeline would model Rotpriest as a Toxic 1 creature but would miss that its real value is punishing interaction and converting opponent removal into poison delivery | Venerated Rotpriest (counter-removal as poison source) |
