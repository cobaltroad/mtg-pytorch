# Sisay, Weatherlight Captain — Primer Analysis

**Commander:** Sisay, Weatherlight Captain {W}{U}{B}{R}{G} — 2/2 Legendary Human Soldier
**Color identity:** Five-color (WUBRG)
**Source:** Moxfield primer by Tolaria_East (updated 2026-01-29)
**Theme:** cEDH tutor engine; command-zone tutor chain through rainbow dorks → planeswalker infinite combo win ("Oath of Nicol" line)

---

## Commander Ability (the trigger)

> {W}{U}{B}{R}{G}: Search your library for a legendary permanent card with mana value less than or equal to Sisay's power, put it onto the battlefield, then shuffle.

Key constraints:
- **Power = CMC ceiling** — Sisay's power determines how high up the chain she can search; growing her power (via counters, lords, Cultist of the Absolute) is critical
- **Requires WUBRG mana** — Rainbow dorks that produce all five colors are the primary enabler; the deck is built around resolving one rainbow dork + Sisay
- **Legendary permanents only** — All nonland permanents in the deck are legendary; each functions as both payload and power-booster for subsequent tutors
- **Opposition Agent + Cursed Totem are the worst enemies** — Totem shuts off activated abilities (Sisay's tap), Agent redirects tutored cards to opponents
- **Instant speed activation possible** — End-step tutoring sets up the win for your own turn; primer describes tutoring Selvala at end step then chaining to Emiel on your turn

---

## Primary Synergy Packages

### 1. Rainbow dorks (primary combo enablers)
Creatures and artifacts that tap for all five colors, enabling Sisay's activation:
- **Bloom Tender**: Tap → {1} mana for each color among controlled permanents; with WUBRG represented, makes {W}{U}{B}{R}{G}
- **Faeburrow Elder**: Same as Bloom Tender; vigilance makes it tap without attacking
- **Kinnan, Bonder Prodigy**: Doubles mana from non-land sources → Bloom Tender + Kinnan = double WUBRG; 2 CMC legend Sisay can grab early
- **Chromatic Orrery**: 7-mana "seven-mana Dockside" — taps for WUBRG; cited as the post-ban Dockside replacement
- **Jegantha, the Wellspring**: Companion or main deck; makes WUBRG (with limit on how it's spent)
- **The Cabbage Merchant**: New strong rainbow dork contender for the slot

### 2. Power inflation (raising Sisay's tutor ceiling)
- **Cultist of the Absolute**: {1} → Sisay gets +4 power until end of turn; the only non-board card that can get a "naked" Sisay to spin for any 5+ CMC legend
- **Agatha's Soul Cauldron**: Exile creature with activated ability from graveyard → ALL your creatures gain that ability; Bloom Tender/Faeburrow in graveyard → any tapped creature makes WUBRG; also puts +1/+1 counters on creatures (Sisay grows)
- **Wan Shi Tong, His Dark Materials**: Accumulates +1/+1 counters; blue pip for Sisay power; flash + flying + vigilance utility

### 3. Oath of Nicol (primary win condition)
The three-legend planeswalker lock:
- **Oath of Teferi**: Legendary enchantment; lets planeswalkers activate twice per turn AND blinks a permanent on ETB
- **Nicol Bolas, Dragon-God**: Planeswalker; +1 loots/steals, -8 near-emblem; combined with Aminatou creates infinite activations
- **Aminatou, the Fateshifter**: Planeswalker; +1 draws then puts card to top, -1 flickers a permanent; with Oath of Teferi in play, -1 flickers Aminatou herself → infinite planeswalker activations + infinite flickers
- **Win with**: Saheeli Rai (+1 makes artifact copies, pings indefinitely), Mount Doom (flicker → ping), Orcish Bowmaster (ETB → flash → infinite ETBs via flicker → infinite arrows)

### 4. Emiel / Selvala / Derevi line (secondary infinite mana)
- **Selvala, Heart of the Wilds**: Tap when largest creature ETBs → draw + make mana equal to that creature's power
- **Derevi, Empyrial Tactician**: ETB trigger or combat damage → untap target permanent
- **Emiel the Blessed**: {3}: Flicker a creature → Derevi ETBs → untap Selvala → Selvala makes 4+ mana → pay {3} for Emiel again → net positive mana each cycle → infinite mana + infinite ETBs
- **Setup**: Tutor Selvala end step → tutor Derevi on your turn (untaps Selvala) → tutor Emiel (activate to flicker Derevi, untap Selvala) → chain

### 5. Silence effects (win enablement)
Deployed offensively on combo turn, not just defensively:
- **Silence / Orim's Chant**: Stop opponents from interacting during win attempt; {W} / {W}{W}
- **Kutzil, Malamet Exemplar**: Silence effect on ETB; also draws cards when modified creature deals combat damage
- **Teferi, Time Raveler**: Restricts opponents to sorcery speed (no instant-speed interaction during your turn)
- **Defense Grid**: Spells cost {3} more when not your turn; benefits Sisay's long game vs. turbo decks

### 6. Free counterspells (commander-centric)
- **Fierce Guardianship**: Free when you control a commander
- **Deflecting Swat**: Free redirect when you control a commander
- **Mindbreak Trap**: Free exile (storm count ≥ 3); stops chain-spell wins
- **Flusterstorm, Swan Song, Mental Misstep**: Cheap interaction protecting win attempts

---

## Combo Lines

### Line 1: Oath of Bolas (primary win)
- Requirements: Oath of Teferi in play, Nicol Bolas Dragon-God in play, Aminatou in play
- Loop: Aminatou -1 flickers Aminatou herself (under Oath of Teferi she can activate twice) → infinite planeswalker activations → infinite flickers of any permanent
- Win with: Saheeli Rai +1 (artifact copies that deal damage), Mount Doom (flicker → ping), Orcish Bowmaster (ETB per flicker → infinite arrow damage)

### Line 2: Emiel + Selvala + Derevi (infinite mana)
- Requirements: Sisay at 4 power, one rainbow dork for activation
- Tutor chain: End step → Selvala; your turn → Derevi (Derevi ETB untaps Selvala → now Selvala is untapped) → Emiel (activate {3}: flicker Derevi → untap Selvala → Selvala makes ≥ 4 mana → net mana positive → loop)
- Result: Infinite mana → tutor everything in deck; win with Saheeli or any activated drain

### Line 3: Agatha's Soul Cauldron + dead Bloom Tender
- Requirements: Bloom Tender or Faeburrow in graveyard, Agatha's Soul Cauldron in play, any creature
- Effect: All your creatures gain Bloom Tender's tap ability → any creature makes WUBRG (if you control permanents of each color) → Sisay activation from any creature
- Enables: Sisay tutor chains without needing to protect the specific rainbow dork

### Line 4: Ertai Resurrected lock
- Requirements: Infinite mana + flicker effect
- Flicker Ertai Resurrected repeatedly → each ETB counters or destroys a spell/permanent → table is locked out until you win
- Note: Also kills The One Ring (animate with Karn, flicker Ertai to "destroy" it, force opponent to overdraw)

---

## Deckbuilding Principles Stated by Author

1. **One rainbow dork + Sisay = game plan** — The deck's floor is extraordinarily low: resolve a mana dork, keep Sisay, start spinning. Every other card is redundant backup.
2. **Legends only in 99** — Every nonland permanent must be legendary; each is both a win condition and a power-booster for Sisay's next activation.
3. **Cultist of the Absolute is the best Sisay piece** — +4 power for {1} on any Sisay build; the only card that lets "naked" Sisay reach 5+ CMC tutoring without any board state.
4. **Kinnan as "hidden" secondary commander** — With mana dorks in play, infinite mana → Kinnan's activated ability finds any non-human creature from library; win without Sisay at all.
5. **Agatha's Soul Cauldron as post-ban Dockside substitute** — Exiling Bloom Tender to Cauldron makes every creature a rainbow dork; dramatically increases redundancy for WUBRG activation.
6. **Silence on combo turn, not opponent's turn** — Silence is not a reactive counterspell here; it's held until your win attempt to stop the table from stopping you.
7. **Opposition Agent / Cursed Totem are first-priority removal targets** — Totem shuts off Sisay's tap ability entirely; Agent turns tutors against you; Strix Serenade is specifically included for these.

---

## Synergy Gaps vs. Current Pipeline

| Gap | Description | Example cards |
|---|---|---|
| Legendary tutor chain (power-as-CMC-ceiling) | Sisay's ability uses her power as a mana value threshold for tutoring; power is a resource to be inflated (not a combat stat); pipeline has no model for "power as tutor ceiling" | Sisay (the hub), Cultist of the Absolute (power inflator), all legends in 99 (tutor targets that also increase power) |
| Rainbow dork — WUBRG all-colors tap | Creatures that produce mana for each color among controlled permanents; a specific category distinct from generic ramp; the presence of WUBRG mana enables Sisay activation | Bloom Tender, Faeburrow Elder (producers); Kinnan (doubler); Sisay (consumer) |
| Agatha's Soul Cauldron ability transfer | Exile creature with activated ability → all your creatures gain that ability; any activated ability creature in graveyard becomes a permanent anthem for the whole board | Agatha's Soul Cauldron (the enabler); Bloom Tender / Faeburrow Elder (the exiled sources); any creature (the recipient) |
| Oath of Teferi planeswalker double-activation | Oath of Teferi lets planeswalkers activate twice; combined with a self-flickering planeswalker (Aminatou) creates infinite planeswalker activations | Oath of Teferi (the doubler); Aminatou (the self-fliickerer); Nicol Bolas (the win enabler) |
| Silence-as-offense (not defense) | Silence / Orim's Chant used proactively on combo turn to prevent interaction; not a counterspell but a "your turn is mine" enabler during win attempt | Silence, Orim's Chant, Kutzil (ETB silence), Teferi Time Raveler (sorcery restriction) |
| Kinnan doubling non-land mana | Kinnan, Bonder Prodigy: whenever a nonland permanent produces mana, it produces that much more; doubles all rainbow dork output; transforms 2-mana activations into 4-mana surpluses | Kinnan (the doubler); all mana dorks + rocks (the doubled producers) |
