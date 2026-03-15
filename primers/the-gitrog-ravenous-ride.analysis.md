# The Gitrog, Ravenous Ride — Primer Analysis

**Commander:** The Gitrog, Ravenous Ride {2}{B}{G} — 6/6 Frog Beast, Haste, Trample, Saddle 1
**Color identity:** Golgari (BG)
**Source:** Moxfield primer by Hydrax (last updated 2026-02-18)
**Theme:** Saddle + sacrifice big-power creatures → massive landfall cascade; token win via combat

---

## Commander Ability (the trigger)

> Saddle 1
> Whenever The Gitrog, Ravenous Ride deals combat damage to a player, you may sacrifice a creature that saddled it this turn. If you do, draw X cards, then put up to X land cards from your hand onto the battlefield tapped, where X is the sacrificed creature's power.

Key constraints:
- **Saddle** is sorcery-speed (during main phase before combat)
- **Sacrifice is optional** after damage — but almost always correct
- **X = sacrificed creature's power** — drives the deckbuilding imperative: maximize saddler power stat
- **Lands enter tapped** — Spelunking changes this to untapped (huge mana advantage)
- **Multiple saddlers**: Tap all eligible creatures to saddle with backups in case removal hits one

---

## Primary Synergy Packages (by the primer author's own labels)

### 1. Frog Fatties (high-power, low-utility creatures)
Creatures chosen specifically for high power-to-cost ratio, ignoring their often-bad abilities:
- **Cheap high-power**: Lupine Prototype (6/5 for {1}{R}), Sheltering Ancient (5/6 for {1}{G}), Yargle and Multani (9/3 for {4}{G}), Phyrexian Soulgorger (6/6 for {2}), Hunted Bonebrute
- **Power amplifiers**: The Skullspore Nexus (pay {2}: double a non-token creature's power; creates token of equal power), Zopandrel Hunger Dominus (doubles power/toughness at beginning of combat), Bristly Bill Spine Sower (add +1/+1 counters via landfall)

**Model insight:** "High power, irrelevant or negative ability" is a distinct card selection pattern. The model has no way to learn that Phyrexian Soulgorger is good in Gitrog despite its terrible ability.

### 2. Landfall payoffs
After Gitrog's ability resolves, multiple lands hit the battlefield simultaneously — all landfall triggers fire:
- **Token generators**: Greensleeves Maro-Sorcerer (creates a Wurm per land), Rampaging Baloths (creates a Beast per land), Scute Swarm, Field of the Dead (Zombies per 7 named lands)
- **Direct damage**: Retreat to Hagra (deals 1 damage or gains 1 life per land), Ob Nixilis the Fallen (3 damage + 3 counters per land)
- **Mana generation**: Tireless Provisioner (creates a Treasure or Food per land), Wayward Swordtooth (extra land plays)
- **Counter generation**: Bristly Bill, Earthbender Ascension

**Model insight:** Mass simultaneous land drops (5+ at once) fire landfall triggers proportionally — a single Gitrog swing can generate 10+ landfall triggers. The model should learn that high-power saddlers correlate positively with these payoffs.

### 3. Ramp density
The deck runs ~28 ramp pieces, enabling Gitrog + a fatty by turn 4 reliably:
- **Turn-1 dorks**: Birds of Paradise, Llanowar Elves, Elvish Mystic, Delighted Halfling
- **Turn-2 land ramp**: Farseek, Nature's Lore, Three Visits, Rampant Growth, Shared Roots
- **Kodama of the East Tree**: Passive land-into-play from hand whenever a permanent ETBs — synergy with token generators creating land-ETB loops

### 4. Recursion package
Fatties are eaten by Gitrog; recursion brings them back:
- **Hand return**: Phyrexian Reclamation, Honest Rutstein (ETB reanimation)
- **Direct reanimate**: Sheoldred Whispering One (upkeep reanimate), Atzal Cave of Eternity (activated ability reanimate), The Soul Stone

---

## Combo Lines

### Kodama of the East Tree + Golgari Rot Farm + token-maker (infinite landfall)
- Requirements: Kodama on field, Golgari Rot Farm in hand, any landfall token-maker (Field of the Dead, Rampaging Baloths, Greensleeves, Scute Swarm, or Tireless Provisioner)
- Loop:
  1. Play Golgari Rot Farm → triggers Rot Farm (bounce itself), Kodama (put 0-CMC permanent into play), token-maker
  2. Resolve Kodama trigger last: put Golgari Rot Farm from hand into play
  3. Resolve Rot Farm: bounce itself to hand
  4. Resolve token-maker: create a creature token
  5. Token ETB triggers Kodama again → put Rot Farm back into play → repeat
- Result: Infinite landfall triggers, infinite token generation
- Win condition: With Ob Nixilis the Fallen or Retreat to Hagra on field, infinite damage to opponents without attacking

**Notes:**
- Removing Kodama of the East Tree makes the deck completely combo-free
- Reanimation package can retrieve Kodama if countered or removed

---

## Deckbuilding Principles Stated by Author

1. **Open hand assessment**: Need at least one ramp spell + one fatty + 2-3 lands; less than 2 lands = mulligan.
2. **Saddle with multiple creatures when possible** — Insurance against removal of the saddler before Gitrog triggers.
3. **Gitrog's commander tax is irrelevant** — Mass land dumps into play offset any additional casting cost within 1-2 turns.
4. **Don't play Gitrog until you have the fatty ready** — Minimize window for opponents to respond to the combo swing.
5. **Spelunking is essential**: Changes tapped land entry to untapped, enabling the dumped lands to produce mana immediately.

---

## Synergy Gaps vs. Current Pipeline

| Gap | Description | Example cards |
|---|---|---|
| Saddle mechanic | Tapping creatures to enable a mount's abilities; saddler's power stat matters for the draw/land trigger | The Gitrog (consumer), all high-power creatures (producers via power stat) |
| High-power-for-cost valuation | Cards with negative or irrelevant abilities chosen purely for power stat; model assigns low synergy scores to these | Yargle and Multani, Phyrexian Soulgorger, Lupine Prototype |
| Simultaneous mass landfall | Multiple lands entering at once multiply landfall triggers proportionally | Rampaging Baloths, Greensleeves Maro-Sorcerer, Scute Swarm, Field of the Dead |
| Power-doubling | Cards that double a creature's power (temporarily or permanently) for combat/sacrifice purposes | The Skullspore Nexus, Zopandrel Hunger Dominus (multiply saddler value) |
| Kodama land-ETB loop | Kodama's "permanent ETB → put 0-CMC card into play" creates land loop with Golgari Rot Farm; very specific interaction | Kodama of the East Tree + Golgari Rot Farm |
