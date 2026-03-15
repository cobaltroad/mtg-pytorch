# Indominus Rex, Alpha — Primer Analysis

**Commander:** Indominus Rex, Alpha {1}{U}{B}{G} — Sultai, Legendary Dinosaur
**Color identity:** Sultai (UBG)
**Source:** Moxfield primer by lunar787 (last updated 2025-09-15)
**Theme:** +1/+1 counter synergies + keyword soup via ETB discard; Bracket 4 optimized

---

## Commander Ability (the trigger)

> When Indominus Rex, Alpha enters the battlefield, discard any number of creature cards. For each card discarded, put a +1/+1 counter on Indominus for each keyword ability that creature had.
> Indominus Rex, Alpha has all keyword abilities of creatures discarded this way.

Key constraints:
- **Discard** is the fuel — creatures held in hand are often more valuable as discard fodder than played
- **Keyword stacking**: Sire of Seven Deaths (6 keywords) + Nightveil Predator (3 non-overlapping keywords) = 9 unique keywords
- The ETB draw (post-counter placement, counters → card draws) rewards stacking many counters
- Build **pivoted (9/2025) away from keyword assembly to +1/+1 counter synergies** as the core game plan; Indominus is now an explosive burst engine rather than the primary win condition

---

## Primary Synergy Packages (by the primer author's own labels)

### 1. +1/+1 Counter engines (the current focus post-9/2025)
Counters grow the whole board and fuel Indominus's ETB draw:
- **Mana dorks that scale with counters**: Devoted Druid (untap via -1/-1 counter), Gyre Sage (evolve), Incubation Druid (add mana per counter), Kami of Whispered Hopes (mana = counters), Crystalline Crawler (store/spend counters for mana)
- **Counter doublers**: Arwen, Weaver of Hope; Ouroboroid; Master Biomancer; Vorinclex, Monstrous Raider
- **Counter payoffs**: Proft's Eidetic Memory (card draw per counter on commander), Sage of Hours (extra turns), Unspeakable Symbol (spend life for counters)

**Model insight:** Crystalline Crawler is a combo piece for multiple commanders — it stores mana as counters and spends them later. This "mana battery" function is not captured by current synergy patterns.

### 2. Keyword soup (secondary, declining emphasis)
The ETB still benefits from keyword-dense discard:
- **High-keyword cards kept as discard fodder**: Sire of Seven Deaths (6 keywords: Menace, Vigilance, First Strike, Lifelink, Reach, Trample), Breaker of Creation (similar)
- **Tutors to find them**: Multiple generic tutors to fetch Sire of Seven Deaths as the baseline keyword dump
- **Nightveil Predator** covers 3 keywords Sire lacks (Deathtouch, Flying, Hexproof)

**Model insight:** Cards like Sire of Seven Deaths are played with the intention of discarding them, not casting them. Their mana cost is effectively 0 in this context — a valuation the model can't currently learn.

### 3. Infinite mana combos
Multiple routes to infinite mana:
- **Incubation Druid + Agatha's Soul Cauldron** (exiling Devoted Druid): Incubation Druid with 3+ counters taps for 3 mana; Soul Cauldron grants Devoted Druid's untap ability; tap for 3, untap with Devoted Druid's ability (pay 1 green), net 2 mana per cycle
- **Crystalline Crawler variants**: Store mana as counters, release for infinite loops

### 4. Infinite turns (Sage of Hours)
- Requirements: Sage of Hours + reliable way to put 5+ counters on it repeatedly
- Counters available via: counter doublers during any creature ETB, proliferate, direct placement
- **Multiple combo images from commanderspellbook.com** referenced in primer for 3 different Sage of Hours lines

---

## Combo Lines

### Line 1: Incubation Druid + Agatha's Soul Cauldron (exiling Devoted Druid)
- Requirements: Incubation Druid with 3+ counters on field, Agatha's Soul Cauldron on field with Devoted Druid exiled
- Loop: Tap Incubation Druid for 3 colored mana → use Soul Cauldron's granted ability to untap it (pay {G}) → net 2 mana per cycle → infinite colored mana

### Line 2: Sage of Hours + 5 counters
- Requirements: Sage of Hours on field, 5 experience-generating effect available
- Loop: Stack 5+ counters on Sage → attack → remove 5 counters → take extra turn → repeat with counter doublers in play

### Line 3: Any infinite mana → Walking Ballista for infinite damage
- Requires: Infinite mana (from Line 1 or other combo) + Walking Ballista in hand
- Win: Cast Ballista for arbitrarily large X, ping opponents to death

---

## Deckbuilding Principles Stated by Author

1. **Early mana dorks are more important than early keywords** — Establish a mana base before Indominus lands.
2. **Sire of Seven Deaths + tutor → discard** is the baseline keyword setup (6 keywords guaranteed).
3. **Holding interaction is essential** — Counter suite (Fierce Guardianship, Deflecting Swat) keeps Indominus alive.
4. **Bracket 4 delineation**: Deliberately excludes Food Chain + Misthollow Griffin and Thassa's Oracle + Demonic Consultation to stay non-cEDH.
5. **Post-9/2025 pivot**: The deck no longer requires Indominus to function; +1/+1 counter engine cards (Devoted Druid, Gyre Sage) form an independent game plan.

---

## Synergy Gaps vs. Current Pipeline

| Gap | Description | Example cards |
|---|---|---|
| Keyword soup synergy | Cards valued for *discarding* due to keyword count, not for casting | Sire of Seven Deaths, Breaker of Creation (discount to {0} in context) |
| Mana-battery counters | Creatures that store mana as +1/+1 counters and spend them later | Crystalline Crawler, Incubation Druid (counter-to-mana conversion) |
| Counter doublers | Cards that double +1/+1 counters placed on any creature | Vorinclex Monstrous Raider, Ouroboroid, Master Biomancer, Arwen Weaver of Hope |
| Sage of Hours extra turn | 5 counters removed → extra turn; highly specific counter threshold trigger | Sage of Hours |
| Agatha's Soul Cauldron + graveyard ability grant | Exiling a creature with activated abilities grants those abilities to creatures with counters | Agatha's Soul Cauldron (combo enabler, not a counter payoff) |
