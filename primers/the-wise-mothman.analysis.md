# The Wise Mothman — Primer Analysis

**Commander:** The Wise Mothman {2}{U}{B}{G} — 3/4 Insect, Flying
**Color identity:** Sultai (UBG)
**Source:** Moxfield primer by JollyCasual (last updated 2024-12-19)
**Theme:** Mill + +1/+1 counters; multiple mill-based combos; combat win secondary; casual/mid-power

---

## Commander Ability (the trigger)

> Whenever one or more cards are put into a player's graveyard from their library, put a +1/+1 counter on each creature you control that doesn't have a +1/+1 counter on it.

Key constraints:
- **"One or more cards"** per mill event — the trigger fires once per mill event, not once per card milled. Single large mill events (e.g., mill 20) give one counter to each creature, while many small mills (e.g., 20 separate mill-1 triggers) give 20 counters per trigger per creature.
- **Smaller, more frequent mill** is strictly better than large single mill bursts
- **Only creatures without a +1/+1 counter** receive one — after all creatures have counters, additional mill events don't add more (until counters are removed or new creatures arrive)
- **Any player's library** — self-mill also triggers Mothman

---

## Primary Synergy Packages (by the primer author's own labels)

### 1. Mill engines
Cards that mill frequently in small amounts per trigger:
- **Altar of the Brood**: Every creature ETB mills each opponent for 1 — mass ETB cascades into repeated Mothman triggers
- **Altar of Dementia**: Sacrifice a creature to mill any player for that creature's power; can chain with tokens
- **Mindcrank**: Damage dealt to an opponent → they mill that many cards; feeds into counter-drawing loops
- **Psychic Corrosion**: Whenever you draw a card, each opponent mills 2 cards

**Model insight:** "Sacrifice a creature to mill" and "damage → mill" are distinct producer patterns. Mothman wants each individual mill event to be small (1-2 cards) and frequent rather than large.

### 2. Counter payoffs (combat win + value)
Mothman's counters build up quickly with repeated mill:
- **Combat win via counters**: Creatures grow large; Mothman itself is a 3/4 flier that quickly becomes 10+/10+ with frequent mill
- **Fathom Mage**: Draw a card whenever it gets a +1/+1 counter — pairs with Psychic Corrosion for a draw-mill loop
- **Generous Patron**: Draw a card when you put a +1/+1 counter on a creature you don't control — if Mothman triggers put counters on opponent creatures... wait, Mothman only buffs YOUR creatures. Generous Patron works differently here.

**Correction from primer:** Generous Patron works in a combo — put counters on *opponent's* creatures via some effect, then draw. In the Mothman loop it's: Fathom Mage gets counter → draw → Psychic Corrosion mills opponents → Mothman triggers → more counters → more draws.

### 3. Combo enablers
The primer describes Mothman as more "combo-capable" than Zellix due to the +1/+1 counter win condition being viable in addition to mill:

**Syr Konrad + Mindcrank loop**:
- Mindcrank: damage → opponent mills
- Syr Konrad: whenever any creature leaves an opponent's graveyard (or goes to a graveyard) → 1 damage to each opponent
- If milled card is a creature → Syr Konrad deals 1 damage → Mindcrank mills 1 more → if creature → repeat
- Not guaranteed infinite but generates significant cascades

**Bloodchief Ascension + Mindcrank**:
- Once Ascension is active (3 quest counters = opponents lose 2 life per graveyard entry), any mill triggers Mindcrank damage triggers Ascension triggers more damage and mill
- True infinite once started

---

## Combo Lines

### Line 1: Zellix (or Scurry Oak) + Altar of the Brood + Altar of Dementia
- Requirements: Zellix or Scurry Oak on field with Mothman, one or both Altars
- Loop: Mill creates creature ETB → Altar of the Brood mills everyone 1 → Mothman triggers (counter on Scurry Oak if non-land) → Scurry Oak makes 1/1 → 1/1 ETB triggers Altar of the Brood → more mill → repeat
- Not infinite but generates large cascades; Altar of Dementia as cleanup

### Line 2: Syr Konrad + Mindcrank
- Requirements: Both on field plus any damage source or mill trigger to start
- Loop: Deal 1 damage → mill 1 → if creature milled, Konrad deals 1 damage per opponent → Mindcrank mills more → cascade
- Rarely infinite; high damage output

### Line 3: Bloodchief Ascension + Mindcrank (true infinite)
- Requirements: Ascension online (3 quest counters), Mindcrank on field, deal any damage to start
- Loop: Damage → mill → 2 life loss → 2 damage → mill 2 → repeat
- Win: Mill out all opponents or drain them to 0

### Line 4: The Wise Mothman + Fathom Mage / Generous Patron + Psychic Corrosion
- Requirements: Mothman + Fathom Mage + Psychic Corrosion
- Loop: Fathom Mage gets counter → draw → Psychic Corrosion mills opponents → Mothman triggers → counter on Fathom Mage → draw again
- Win: Mill out all opponents; Fathom Mage's draw is optional so you don't deck yourself

### Line 5: Glen Elendra Archmage + The Great Henge + Altar of Dementia (infinite)
- Requirements: All three on field
- Loop: Sacrifice Glen Elendra to Altar → mill for 2 → Persist returns her with -1/-1 counter → Henge puts +1/+1 counter (negates -1/-1) and draws a card → repeat
- Win: Infinite mill + infinite card draw + infinite counterspells (paying {1}{U} each iteration)

### Line 6: The Wise Mothman + Walking Ballista + Mindcrank
- Requirements: All three on field, Walking Ballista with at least 1 counter
- Loop: Ping opponent with Ballista → Mindcrank mills that many cards → if non-land milled, Mothman puts counter back on Ballista → repeat
- Accelerated with counter doublers (Hardened Scales)

### Line 7: Gitrog Monster + Evolution Witness + Psychic Corrosion (graveyard discard loop)
- Requirements: All on field, end turn with oversized hand including a land
- Loop: Discard land at cleanup → Gitrog draws a card → Psychic Corrosion mills each opponent 2 → Mothman gives counters → put counter on Evolution Witness → return land from graveyard → discard at cleanup again
- Win: Mill out opponents; when own library gets thin, discard Eldrazi Titan to reshuffle

---

## Deckbuilding Principles Stated by Author

1. **Frequent small mill beats single large mill** — The trigger fires once per event regardless of cards milled; small repeated triggers maximize counter accumulation.
2. **Watch out for graveyard decks** — Milling an opponent with a graveyard-based strategy may help them win first; prioritize eliminating those players.
3. **Leyline of the Void + mill = still triggers Mothman** — Milling into exile still counts as "from their library," so Leyline provides grave hate without disrupting your own engine.
4. **Mikokoro timing** — Mill on your turn to avoid opponent upkeep shenanigans before they die.
5. **Combat is a real win condition** — Unlike Zellix, Mothman's counter accumulation can make creatures large enough to win through combat damage.

---

## Synergy Gaps vs. Current Pipeline

| Gap | Description | Example cards |
|---|---|---|
| Mill trigger frequency vs. quantity | "Small frequent mill" is better for Mothman than "large single mill"; the pipeline has no way to score this distinction | Altar of the Brood (1 per creature ETB), Mindcrank (damage → mill), vs. large one-shot mills |
| Damage-triggers-mill payoff | Mindcrank converts damage into mill; Syr Konrad converts mill into damage; creates circular loops | Mindcrank, Syr Konrad the Grim |
| Counter-on-ETB → draw → mill chain | Fathom Mage: counter → draw; Psychic Corrosion: draw → opponent mills; Mothman: mill → counter; three-way loop | Fathom Mage, Psychic Corrosion, The Wise Mothman |
| Persist + ETB counter negation | Glen Elendra Archmage's persist (-1/-1 on return) negated by +1/+1 counter from Henge; creates infinite loop | Glen Elendra Archmage (persist creature), The Great Henge (counter cleaner) |
| Graveyard-shuffle Eldrazi | Discarding Emrakul etc. to reshuffle graveyard into library as a safety valve in mill decks | Emrakul, the Aeons Torn (or any Eldrazi titan with shuffle trigger) |
