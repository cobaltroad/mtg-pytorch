# The Wise Mothman — cEDH Primer Analysis

**Commander:** The Wise Mothman {2}{U}{B} — 1/1 Legendary Insect
**Color identity:** Dimir (UB)
**Source:** Moxfield cEDH primer by BIRD7 (updated 2025-02-27)
**Theme:** Food Chain combo with Mothman as the win outlet; cast Mothman infinitely via Food Chain to give all opponents enough Radiation counters to kill them; layered backup lines via Thassa's Oracle + Consultation and Cephalid Illusionist mill; Mothman's double trigger (proliferate → draw) provides an engine in longer games

---

## Commander Ability (the trigger)

> Whenever you proliferate, put a +1/+1 counter on each creature you control.
> Whenever you put one or more +1/+1 counters on a creature, draw a card.

Key constraints:
- **Double trigger chain** — proliferating fires the first trigger; that counter placement fires the second; a single proliferate event causes one counter placement (on each creature) which causes one draw per creature receiving a counter; with one creature in play, one proliferate = one draw
- **Draw scales with board state** — every creature in play draws a card when counters are placed; the draw engine self-reinforces as more creatures enter
- **Primary cEDH use case is Food Chain** — the Radiation win condition (cast Mothman infinitely via Food Chain) uses the commander as the delivery mechanism, not the draw engine; the draw engine is relevant in longer, non-Food-Chain games
- **Radiation is a counter on opponents** — Radiation counters are placed on opponents when Mothman ETBs via Food Chain; "Ghoulify" (opponent loses at 10+ Radiation in their precombat main phase) is a delayed kill condition; opponents may still win before their main phase
- **The Wise Mothman is BUG in identity** but this deck is listed with {U}{B}{G} symbols in the primer; note: the commander's casting cost is {2}{U}{B} — Dimir — but the deck runs BUG (blue/black/green) per the Food Chain package

---

## Primary Synergy Packages

### 1. Food Chain infinite mana engine (primary gameplan)
Food Chain generates infinite creature mana by using exile-recursive creatures as infinite input:
- **Food Chain**: exile a creature to add mana equal to its mana value +1 for creature spells only; if the exiled creature can be cast from exile (Eternal Scourge) or returns from exile (Misthollow Griffin), the loop is infinite
- **Eternal Scourge**: can be cast from exile; exile it to Food Chain for {3}, cast Eternal Scourge for {3}, repeat → generates infinite {U}{B}{G} for creature spells; requires a starting creature of MV ≥ 2
- **Misthollow Griffin**: can be cast from exile; exile it to Food Chain for {5} in {U} for creature spells, cast Misthollow Griffin for {4} → generates infinite {U} for creature spells; filter into {U}{B}{G} via color-producing creatures; requires a starting creature of MV ≥ 3
- **Win condition**: cast The Wise Mothman repeatedly from the command zone with infinite creature mana → each ETB puts a Radiation counter on each opponent → repeat until each opponent has 10+ Radiation → opponents die at their precombat main phases

### 2. Thassa's Oracle + Consultation/Pact (backup one-shot win)
- **Thassa's Oracle + Demonic Consultation**: cast Oracle, hold priority on ETB, cast Consultation naming a card not in the deck (e.g., The Most Dangerous Gamer) → entire library is exiled → Oracle's devotion check sees an empty library → win; total cost {U}{U}{B}
- **Thassa's Oracle + Tainted Pact**: same structure; Pact exiles cards until a card of the same name is found (never, in singleton); total cost {1}{U}{U}{B}
- **Praetor's Grasp line**: search an opponent's library for Consultation, Tainted Pact, or Thassa's Oracle to assemble the line using pieces from others' decks; costs {1}{B}{B} or {2}{B}{B}; risky if the opponent doesn't have the piece

### 3. Cephalid Illusionist mill loop (Mothman O's)
The Mothman's proliferate trigger interacts with Cephalid Illusionist's targeting trigger:
- **Cephalid Illusionist**: whenever a player targets it with a spell or ability, mill three cards; repeat targeting mills the library
- **Loop**: Wise Mothman's proliferate trigger (triggered when you proliferate) puts counters on all creatures → targeting Cephalid Illusionist with any ability counts as targeting → Cephalid mills 3 → if the mill produces another proliferate effect or allows continuing, the Mothman trigger fires again as long as Cephalid is targeted → mill entire library
- **Win**: with empty library, Dread Return / Emperor of Bones / Reanimate targets Thassa's Oracle from the graveyard; Oracle ETB with empty library → win
- **Note**: the primer frames this as "Mothman's proliferate trigger targeting Cephalid" — the Mothman trigger puts counters and the targeting mechanism feeds Cephalid; the exact wording suggests exploiting targeting of Cephalid Illusionist with Mothman's triggered ability

### 4. Lilysplash Mentor + Cloud of Faeries + Gaea's Cradle (Frog and Faerie line)
Infinite mana from creature ETB flicker with Cradle:
- **Lilysplash Mentor**: pay to flicker Cloud of Faeries (and any other creature with {U})
- **Cloud of Faeries**: on ETB, untap two lands; untap Gaea's Cradle + a blue-producing land
- **Loop**: tap Gaea's Cradle (produces {G} × creature count) + blue land for {U}; activate Lilysplash Mentor to flicker Cloud of Faeries for {U}, netting {G} mana; repeat for infinite {G}; filter into infinite {U}{B}{G}
- **Win**: infinite flicker of The Wise Mothman (Radiation) or infinite mana for Finale of Devastation; also infinite Orcish Bowmasters triggers (flash on each draw)

### 5. Emperor of Bones + Survival of the Fittest (long toolchain line)
A multi-step toolchain that assembles Food Chain from scratch:
- **Survival of the Fittest**: discard Hoarding Broodlord → find Emperor of Bones; cast Emperor → combat → exile Broodlord with Emperor's ability → adapt Emperor to reanimate Broodlord → Broodlord's ETB searches for Essence Flux; cast Essence Flux on Broodlord → second ETB searches for Saw in Half; cast Saw in Half on Broodlord → two token copies ETB → search for Food Chain and Eternal Scourge; exile a Broodlord token to Food Chain to cast Eternal Scourge → infinite mana → Mothman wins
- This line is the fallback when Food Chain cannot be found directly; most mana-intensive but can be broken across two turns

### 6. Culling Ritual + Mnemonic Betrayal (alternative board-reset win)
- **Culling Ritual**: destroy all nonland permanents with MV ≤ 2; in a cEDH meta of mana rocks and dorks, this generates massive amounts of {B}/{G}; leaves {U} floating
- **Mnemonic Betrayal**: exile all opponents' graveyards and use all cards in them for the turn; after Ritual clears the table, opponents have lost their rocks and dorks which now fill graveyards; cast Betrayal to use those resources to assemble a win
- Requires {2}{U}{B}{G} and a board state where opponents have enough MV ≤ 2 permanents to generate the mana to cast Betrayal afterward

### 7. Intuition + Shifting Woodland lock (toolbox delivery)
- **Intuition**: search for three cards; the opponent decides which one you keep; but with Shifting Woodland and Noxious Revival in the mix, all three results give you the card you want
- **Line**: tutor for (target nonland permanent) + Shifting Woodland + Noxious Revival → if opponent gives you the target, cast it; if opponent gives you Woodland, animate it as the target; if opponent gives you Revival, put the target on top and draw it → outcome is always the desired card
- Typically used to find Food Chain or Survival of the Fittest; cast at instant speed in an opponent's end step

---

## Combo Lines

### Line 1: Food Chain + Eternal Scourge (primary win)
- Requirements: Food Chain in play, any creature with MV ≥ 2 in play or hand, The Wise Mothman available in command zone
- Loop: Exile MV ≥ 2 creature to Food Chain → generate creature mana equal to MV +1 → cast Eternal Scourge (from exile, MV 3, costs 3) → exile Eternal Scourge to Food Chain → generate {4} for creatures → cast Eternal Scourge → repeat → net one mana of {U}{B}{G} per loop → accumulate infinite {U}{B}{G} for creature spells → cast The Wise Mothman from command zone repeatedly
- Result: Each Mothman casting adds Radiation counters to all opponents; continue until all opponents have 10+ Radiation
- Win: Opponents die at their precombat main phases; note that Borne Upon a Wind, Demonic Consultation, or Tainted Pact in opponents' hands can cause a loss during that window — cast Mothman when opponents have no mana up if possible

### Line 2: Thassa's Oracle + Demonic Consultation or Tainted Pact
- Requirements: Thassa's Oracle in hand, Demonic Consultation or Tainted Pact in hand, {U}{U}{B} or {1}{U}{U}{B} available
- Sequence: Cast Thassa's Oracle → hold priority on ETB trigger → cast Consultation naming The Most Dangerous Gamer (not in deck) or cast Tainted Pact → entire library exiles → Oracle ETB resolves → library is empty → Oracle checks devotion to blue against cards left in library (zero) → win
- Result: Instant win; no delay; no opponent window
- Win: Cleanest line in the deck; unaffected by the Radiation timing issue

### Line 3: Mothman O's (Cephalid Illusionist mill)
- Requirements: The Wise Mothman on field, Cephalid Illusionist on field, any targeting ability available
- Loop: Target Cephalid Illusionist with any spell/ability → Cephalid mills 3 → if mill hits non-land cards, continue targeting → Mothman's proliferate counter placement triggers draw; mill entire library
- Result: Empty library; full graveyard with Thassa's Oracle and reanimation spells
- Win: Cast Dread Return / Reanimate / Emperor of Bones targeting Thassa's Oracle from graveyard → Oracle ETB with empty library → win

### Line 4: Food Chain + Misthollow Griffin (variant)
- Requirements: Food Chain in play, any creature with MV ≥ 3 in play, Misthollow Griffin in hand
- Loop: Exile MV ≥ 3 creature to Food Chain → cast Misthollow Griffin (from exile, MV 4, costs {1}{U}{U}{1} = 4, Food Chain provides {U}{U}{U}{U}{U} for creatures) → exile Misthollow to Food Chain → generate {5}{U} for creatures → cast Misthollow → net one {U} per loop → generate infinite {U} for creatures → filter via other mana producers into {U}{B}{G} → cast Mothman repeatedly
- Win: Same Radiation accumulation as Line 1

---

## Deckbuilding Principles Stated by Author

1. **Win condition in the command zone** — unlike Atraxa Grand Unifier or The First Sliver (Food Chain decks where the finisher is in the 99), Mothman's Radiation mechanic means the commander itself is the repeating damage source; this frees up deck slots and ensures the win condition is always available.
2. **Layered redundant win conditions** — three distinct paths (Food Chain Radiation, Thassa's Oracle consultation, Cephalid Illusionist mill); the deck is hard to hate out because answering one line opens another.
3. **Radiation timing weakness is a known vulnerability** — opponents do not die instantly; they die at their precombat main phase; opponents with instant-speed win conditions (Borne Upon a Wind, Tainted Pact) may win before dying; cast Mothman when opponents are tapped out to minimize this window.
4. **Flexibility through opponents' The One Ring** — a specific strength called out explicitly: the Radiation win works through opponents resolving The One Ring (protection from everything doesn't prevent Radiation counters being placed since Radiation is added on cast, not via targeting).
5. **Crime-committing synergies and Cephalid Illusionist** — Mothman's unique interaction profile with "commit a crime" cards and Cephalid Illusionist is listed as a reason to play this commander over alternatives; these interactions are not available in non-Dimir Food Chain decks.
6. **Mulligan aggressively based on seat order** — the primer provides detailed mulligan guides based on turn order position and metagame pod composition; the ideal seven contains either a Food Chain piece or an Oracle + consultation piece.
7. **Self-Radiation is a real cost** — using Food Chain + Mothman gives YOU Radiation counters as well; the deck accepts this cost but must track that the pilot is also on the clock.

---

## Synergy Gaps vs. Current Pipeline

| Gap | Description | Example cards |
|---|---|---|
| Proliferate-as-draw engine double trigger | The Wise Mothman's two abilities chain: proliferating fires the first (put counters on creatures), counter placement fires the second (draw a card); the pipeline can model each trigger independently but cannot model the chain — it sees "proliferate" and "put counters on creatures" as separate synergy edges, not as a self-reinforcing loop where one event always produces the other | The Wise Mothman's second trigger is invisible unless the pipeline knows the first trigger always produces the condition for the second |
| Food Chain mana restriction | Food Chain produces mana only for creature spells; it pairs with exile-recursive creatures (Eternal Scourge, Misthollow Griffin) to generate infinite creature mana; the pipeline cannot detect the "mana for creatures only" restriction or know that Eternal Scourge being castable from exile is what makes the loop work | Food Chain + Eternal Scourge: the synergy is in the exile zone, not in oracle text interactions between the two cards |
| Radiation as delayed kill (timing window) | Radiation counters cause opponents to lose at their precombat main phase; this is a deferred win condition with a gap window where opponents can win first; the pipeline models Radiation as a counter type that proliferate advances, but cannot model the timing vulnerability or the implications for when to use the combo | The Wise Mothman (Radiation timing): opponents die on their turn, not immediately |
| Commander-zone cycling as repeated ETB | Food Chain + Mothman works by sending Mothman back to the command zone and recasting; each recasting costs {2}{U}{B} + commander tax but is funded by infinite creature mana; the pipeline has no model for "commander zone recycling as a combo component" — it cannot know that casting the same legendary creature 10 times is the intended win condition | The Wise Mothman (command zone as combo zone): each recast is a meaningful game action, not commander tax avoidance |
| Oracle + Consultation as two-card instant win | Thassa's Oracle + Demonic Consultation is a canonical cEDH two-card combo; the synergy requires understanding that Consultation exiles your library before Oracle's ETB resolves, leaving Oracle checking devotion vs. zero remaining cards; this is a rules-timing interaction, not an oracle-text pattern | Thassa's Oracle + Demonic Consultation (library exile + ETB stack interaction invisible to regex) |
| Cephalid Illusionist targeting loop | Cephalid Illusionist mills when targeted by any spell or ability; in combination with Mothman's counter-placing proliferate trigger, targeting the Illusionist with the Mothman ability creates a mill loop; the pipeline cannot model "targeting this creature is beneficial" — it only sees "mill 3" as an effect | Cephalid Illusionist (value from being targeted, not from dealing damage or ETB) |
| Intuition + Shifting Woodland lock | Intuition's value in this deck comes from a three-card pile where every decision the opponent makes gives you the same card; Shifting Woodland + Noxious Revival ensure that regardless of which card the opponent gives you, you get the desired nonland permanent; the pipeline models Intuition as a tutor but cannot model the "bulletproof decision tree" |  Intuition + Shifting Woodland + Noxious Revival (triple-card redundancy in a single tutor) |
| Self-Radiation symmetry | Using Food Chain + Mothman to win also places Radiation counters on the controller; the pilot must track their own Radiation and win before running out of time; the pipeline has no concept of symmetric costs or of a win condition that also races against the controller | The Wise Mothman (self-Radiation as a clock on the pilot's own game state) |
