# Baylen, the Haymaker — Primer Analysis

**Commander:** Baylen, the Haymaker {R}{G}{W} — 3/3 Legendary Creature — Human Citizen
**Color identity:** Naya (RGW)
**Source:** Moxfield primer by SteppinRazor
**Theme:** cEDH combo; any token entering triggers a tap/untap on a permanent; infinite tokens → infinite mana → draw entire deck → win via Lightning Bolt loop, Blind Obedience drain, or Voldaren Epicure pings

---

## Commander Ability (the trigger)

> Whenever a token enters the battlefield under your control, you may tap or untap target permanent.

Key constraints:
- **Any token type counts** — creature tokens, Treasure tokens, Food tokens, Blood tokens, etc. all trigger the ability; the deck deliberately exploits this
- **Tap OR untap, not both** — each trigger gives one choice; the value is in untapping mana sources to generate infinite mana from infinite tokens
- **"May" trigger** — the trigger is optional; Baylen does not force a tap/untap, which matters in loops where you must stop the engine cleanly
- **Targets a permanent, not just a creature** — can untap lands, artifacts, or creatures; the primary use is untapping mana sources (lands or mana rocks) to float mana during infinite token loops
- **Baylen does not need to attack** — purely an ETB/token trigger; the deck wins through non-combat lines

---

## Primary Synergy Packages

### 1. Infinite token combo engines
The deck runs multiple overlapping infinite combos; all of them produce tokens as a byproduct, and those tokens trigger Baylen to untap mana sources:
- **Dockside Extortionist + Emiel the Blessed**: Flicker Dockside with Emiel ({3}{G}) each loop; if Dockside makes ≥ 4 Treasures, the loop nets mana; each Treasure is a token triggering Baylen
- **Dockside Extortionist + Temur Sabertooth**: Return Dockside to hand and recast ({2}{R}); requires Dockside to make ≥ 5 Treasures; each Treasure token triggers Baylen
- **Cloudstone Curio + Dockside Extortionist**: With a second creature on board, bounce the companion each time Dockside ETBs; Cloudstone ETB → return other creature → recast Dockside loop; each Treasure triggers Baylen
- **Dualcaster Mage + Twinflame**: Cast Twinflame targeting any creature; flash in Dualcaster Mage copying Twinflame; each new Dualcaster Mage token copies Twinflame again → infinite haste tokens; each token triggers Baylen
- **Kiki-Jiki, Mirror Breaker + Felidar Guardian**: Kiki-Jiki makes a copy of Felidar Guardian; the copy flickers Kiki-Jiki, untapping him; repeat for infinite haste Felidar Guardian tokens; each token triggers Baylen
- **Kiki-Jiki + Village Bell-Ringer**: Each Village Bell-Ringer token untaps Kiki-Jiki when it ETBs; infinite haste tokens; each token triggers Baylen; also resolves Blind Obedience lockouts by casting Bell-Ringer after generating tapped infinite tokens

### 2. Birthing Pod tutor chain
The primary assembler for the Kiki-Jiki combo:
- Sacrifice Baylen (3-drop) → fetch **Felidar Guardian** (4-drop) to battlefield; Felidar ETB flickers Birthing Pod
- Sacrifice Felidar Guardian → fetch **Karmic Guide** (5-drop); Karmic Guide ETB returns Felidar Guardian; Felidar ETB flickers Birthing Pod again
- Sacrifice Felidar Guardian → fetch **Kiki-Jiki, Mirror Breaker** (5-drop); activate Kiki-Jiki to copy Karmic Guide token; combo assembles
- This chain tutors directly into the Kiki-Jiki + Felidar Guardian win without drawing into pieces

### 3. Passive token generators (Baylen fuel without comboing)
Cards that generate tokens without going infinite, providing Baylen triggers in the midgame for ramp/draw:
- **Smothering Tithe**: Each opponent draw creates a Treasure token → each Treasure triggers Baylen; tapping Treasures for draw instead of cracking them is uniquely enabled by Baylen's untap ability
- **Arasta of the Endless Web**: Each spell an opponent casts triggers a Spider token; in a multi-player game this generates significant token flow each turn cycle; Baylen untaps a land per Spider
- **Charismatic Conqueror**: Each opponent tapping a permanent (lands, rocks, creatures) during their turn creates a Citizen token; doubles as stax (opponents enter tapped); each Citizen triggers Baylen

### 4. Stax package (light)
- **Blind Obedience**: Forces opponent artifacts and creatures to enter tapped; also drains life each time mana is spent on extort; used as an alternative win condition when generating infinite mana
- **Charismatic Conqueror**: Light stax; forces tapped entry on opponent permanents while also generating tokens for Baylen

### 5. Mana generation and deck access
Once any infinite token loop is active, Baylen's trigger provides unlimited untap targets:
- Each token triggers Baylen → untap a land or mana rock → tap it again for mana → pay for the next token creation; infinite mana in whatever colors the untapped permanents produce
- After infinite mana, **Voldaren Epicure** looped with Emiel or Temur Sabertooth creates a Blood token each ETB → each Blood token triggers Baylen → drain each opponent 1 life per loop
- **Eternal Witness** looped with Emiel/Sabertooth + **Lightning Bolt**: Bolt an opponent; flicker/bounce Witness; Witness returns Bolt; repeat; kill table with infinite damage

### 6. Instant-speed win access
- **Crop Rotation + Emergence Zone**: Fetch Emergence Zone at instant speed; once the zone is activated, any of the non-combat win lines (Blind Obedience extort loop, Lightning Bolt loop, Voldaren Epicure drain) can be executed on any player's turn

---

## Combo Lines

### Line 1: Dockside Extortionist + Emiel the Blessed (infinite Treasure tokens)
- **Requirements:** Dockside Extortionist on field; Emiel the Blessed on field; opponents control ≥ 4 artifacts/enchantments combined; Baylen in play
- **Loop:** Pay {3}{G} to activate Emiel, flickering Dockside → Dockside ETB creates Treasure tokens (≥ 4); each Treasure triggers Baylen → untap a land or Emiel's mana source; crack one Treasure + tap untapped lands to replay {3}{G}; repeat
- **Result:** Infinite Treasure tokens; infinite colorless mana (and any color from Treasures or untapped colored sources); each Treasure token triggers Baylen once
- **Win:** Cast Voldaren Epicure loop to ping opponents, or draw deck and cast Lightning Bolt loop

### Line 2: Dualcaster Mage + Twinflame (infinite haste creature tokens)
- **Requirements:** Twinflame in hand; Dualcaster Mage in hand; {1}{R} to cast Twinflame; {1}{R}{R} to flash in Dualcaster Mage; Baylen in play
- **Loop:** Cast Twinflame targeting any creature; while Twinflame is on the stack, flash in Dualcaster Mage copying Twinflame; the copy creates another Dualcaster Mage token; each new Dualcaster Mage copies the previous Twinflame copy; chain continues creating infinite Dualcaster Mage tokens with haste; each token triggers Baylen
- **Result:** Infinite Dualcaster Mage tokens with haste; infinite Baylen triggers (infinite mana from untapping sources); entire deck accessible
- **Win:** Attack with infinite haste tokens, or use the mana to execute Lightning Bolt / Blind Obedience lines; author recommends playing Grand Abolisher + Sigarda before moving to combat

### Line 3: Kiki-Jiki, Mirror Breaker + Felidar Guardian (classic infinite)
- **Requirements:** Kiki-Jiki on field; Felidar Guardian on field; Baylen in play
- **Loop:** Tap Kiki-Jiki to create a haste copy of Felidar Guardian; copy ETB flickers Kiki-Jiki, untapping him; Kiki-Jiki untap creates another copy; infinite haste Felidar Guardian tokens; each token triggers Baylen
- **Result:** Infinite haste tokens; infinite Baylen triggers → infinite mana
- **Win:** Attack for lethal, or use mana for Lightning Bolt / drain loops

### Line 4: Voldaren Epicure loop (infinite life drain, non-combat win)
- **Requirements:** Infinite mana; Emiel the Blessed or Temur Sabertooth on field; Voldaren Epicure on field or in hand; Baylen in play
- **Loop:** Flicker or bounce-and-recast Voldaren Epicure each iteration; each ETB creates a Blood token and deals 1 damage to each opponent; each Blood token triggers Baylen → untap a mana source; repeat
- **Result:** Unlimited direct damage to all opponents simultaneously; does not require attacking
- **Win:** Deal enough damage to kill all three opponents

---

## Deckbuilding Principles Stated by Author

1. **Any token type is valid** — the deck deliberately runs non-creature token generators (Smothering Tithe for Treasures, Arasta for Spiders, Charismatic Conqueror for Citizens) because Baylen's trigger does not care about token type; this expands the card pool far beyond creature-token decks
2. **Prefer instant-speed flicker over sorcery-speed bounce** — Emiel the Blessed (instant-speed flicker) is strictly better than Temur Sabertooth (requires recasting) because it does not require additional mana for the cast step and can be activated at any time
3. **Leverage Emergence Zone for off-turn wins** — Crop Rotation into Emergence Zone lets the deck execute non-combat win lines on any player's turn, making counterspell protection easier to time
4. **Light stax is supplementary** — Blind Obedience and Charismatic Conqueror are included but the primary game plan is comboing off, not locking opponents out; they generate incidental value (tokens for Baylen, drain damage) rather than being the win condition themselves
5. **Birthing Pod chain is the backup tutor line** — the author explicitly details the 3-drop → Felidar Guardian → Karmic Guide → Kiki-Jiki chain as a reliable pod sequence using Baylen himself as the initial sacrifice

---

## Synergy Gaps vs. Current Pipeline

| Gap | Description | Example cards |
|---|---|---|
| Token-type agnostic trigger | Baylen triggers on any token type (Treasure, Spider, Citizen, creature), but the pipeline models synergy by matching oracle text keywords; non-creature token types (Treasure, Food, Blood) produced by one card are not linked to a Baylen-style "any token" trigger consumer | Smothering Tithe (Treasure tokens) → Baylen; Arasta of the Endless Web (Spider tokens) → Baylen |
| Tap/untap-on-token-ETB as ramp | Baylen's untap ability is an activated/replacement effect that converts token ETBs into mana; no oracle text on the mana source says "when a token enters, untap me" — the relationship is entirely through Baylen's trigger resolution; the pipeline cannot model "token → untap land → float mana" as a three-card synergy chain | Baylen + any token producer + any land/mana rock |
| Flicker-loop combo identification | The Dockside + Emiel / Kiki-Jiki + Felidar / Dualcaster + Twinflame combos require recognizing that flickering a creature resets its ETB trigger; the pipeline sees ETB text on individual cards but cannot chain "flicker target → ETB fires again → net mana positive" as an infinite loop | Emiel the Blessed + Dockside Extortionist; Kiki-Jiki + Felidar Guardian |
| Birthing Pod tutor chain | The Pod chain (sacrifice CMC N → fetch CMC N+1 to battlefield) is a positional tutoring sequence; the synergy between sacrificing Baylen (3-drop) to find Felidar Guardian (4-drop) to find Karmic Guide (5-drop) to find Kiki-Jiki is entirely determined by converted mana cost adjacency, not oracle text similarity | Birthing Pod + Baylen + Felidar Guardian + Karmic Guide + Kiki-Jiki |
| "When you do" reflexive ability (Baylen untap) | Baylen's tap/untap is a may-trigger that resolves as part of the token ETB trigger resolution; it is not a separate triggered ability on any other card; the pipeline cannot identify that "Emiel creates a token copy → Baylen triggers → untap Emiel's mana source" is a self-sustaining loop | Emiel the Blessed + Baylen (untap Emiel mana source each flicker cycle) |
| Stax-as-token-generator interaction | Charismatic Conqueror creates tokens when opponents tap permanents; this is a triggered ability on an opponent's action (tapping a mana source), not a triggered ability on the Conqueror card itself; the pipeline cannot connect "opponent taps a land" to "Baylen triggers" | Charismatic Conqueror → Citizen token → Baylen untap trigger |
| Instant-speed win via Emergence Zone | The Crop Rotation → Emergence Zone line lets the deck win at instant speed on any player's turn; the synergy is between a land tutor, a specific utility land, and a non-combat win condition already assembled; the pipeline has no model of "win condition assembled + protection window" | Crop Rotation + Emergence Zone + Voldaren Epicure / Lightning Bolt loop |
