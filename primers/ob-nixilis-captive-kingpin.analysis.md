# Ob Nixilis, Captive Kingpin — Primer Analysis

**Commander:** Ob Nixilis, Captive Kingpin {2}{B}{R} — 3/4 Demon, Flying, Trample
**Color identity:** Rakdos (BR)
**Source:** Moxfield primer by Lumautis (last updated 2025-10-21)
**Theme:** 1-damage pinger triggers for card draw + exiling + combo engine; Rakdos combo

---

## Commander Ability (the trigger)

> Whenever one or more opponents each lose exactly 1 life, exile the top card of your library. You may play that card this turn, and you gain +1/+1 counter.
> Flying, Trample.

Key constraints:
- **"Exactly 1" life loss** — triggers only when a single point of damage/life loss goes to each opponent; 2+ damage to one opponent from a single source does NOT trigger
- **"One or more opponents"** — hitting multiple opponents with separate 1-damage pings still only gives one trigger per event
- **Exile play** — cards exiled off the top can be played that turn (impulse draw); fuel for artifact interactions

---

## Primary Synergy Packages (by the primer author's own labels)

### 1. Pingers — 1-damage producers
Cards that repeatedly deal exactly 1 damage:
- **Classic pingers**: Kessig Flamebreather (non-creature spells → 1 damage), Reckless Fireweaver (artifacts → 1 damage), Hedron Detonator (colorless spent → 1 damage)
- **Broader trigger pingers**: Black Mage's Rod, Vivi's Persistence, Kederekt Parasite — trigger on non-creature spells (artifacts + enchantments + instants + sorceries)
- **Kederekt Parasite**: Each opponent draws a card → deal 1 damage; excellent with any wheel effect

**Model insight:** "Exactly 1 damage" pingers are a distinct sub-category from burn spells. The constraint is precise: regular damage spells don't trigger Ob Nixilis unless they deal exactly 1 to each opponent.

### 2. Zero-mana artifacts / free spells
0-mana artifacts enable pingers (Reckless Fireweaver, Hedron Detonator) at minimal cost:
- **Moxen replacements**: Jeweled Amulet, Everflowing Chalice (0-mana for turn-1 plays)
- **Sensei's Divining Top**: Cast for free-ish with mana generation, triggers Fireweaver/Detonator
- **Birgi, God of Storytelling**: Generates {R} on each spell cast, enables chaining 0-cost spells
- **Glaring Fleshraker**: Each colorless artifact cast → make colorless mana + ping each opponent for 1

**Model insight:** The synergy between 0-mana artifacts and pingers is a specific producer→consumer chain: 0-cost artifacts are producers (trigger Reckless Fireweaver/Glaring Fleshraker), and those pingers feed Ob Nixilis.

### 3. Card draw engines
Ob Nixilis already provides impulse draw on each trigger, supplemented by:
- **Prosper, Tome-Bound**: Exile draw on each opponent's turn + treasures on exiled cards played
- **Birgi, God of Storytelling** (back face: Harnfel, Horn of Bounty): Discard → exile 2 cards

### 4. Combo win conditions
Multiple routes to infinite via the 1-damage trigger loop:

**Win con 1 — All Will Be One**: Any 1 damage → trigger → +1/+1 counter placed → AWBO deals 1 damage → repeat. Goes infinite immediately with any pinger online.

**Win con 2 — Underworld Breach lines**: Breach + Grinding Station + 0-mana artifact creates mill loop; Walking Ballista or Reckless Fireweaver converts infinite triggers to damage.

**Win con 3 — Agatha's Soul Cauldron + Walking Ballista**: Exile Ballista from graveyard → Ob Nixilis gets Ballista's ping ability → remove counter to ping for 1 → Ob gets a new counter (from his trigger) → repeat infinitely.

---

## Combo Lines

### Line 1: All Will Be One + any pinger
- Requirements: Ob Nixilis on field, any card that deals exactly 1 damage, All Will Be One on field
- Loop: Trigger 1 damage → Ob gets +1/+1 counter → AWBO deals 1 damage to opponent → repeat
- Win: Loop until opponents are dead

### Line 2: Underworld Breach + Grinding Station + Mox + Reckless Fireweaver
- Requirements: All four pieces in play or accessible via Breach from graveyard
- Loop: Sacrifice 0-mana artifact to Grinding Station → mill → Breach returns artifact → recast (Fireweaver triggers) → repeat
- Win: Infinite Fireweaver pings deal 1 damage each

### Line 3: Agatha's Soul Cauldron + Walking Ballista
- Requirements: Agatha's Soul Cauldron with Ballista exiled, Ob Nixilis on field
- Loop: Remove counter from Ob (via Ballista ability granted by Cauldron) to deal 1 damage → Ob's trigger puts +1/+1 counter back on Ob → repeat
- Win: Infinite ping loop

---

## Deckbuilding Principles Stated by Author

1. **Have an engine ready when Ob Nixilis enters** — He's too fragile to cast into a blank board; the setup must exist already.
2. **The "exactly 1 damage" constraint is a feature** — It forces the deck to be built around precise pinger effects rather than generic burn.
3. **Recent bans context**: Post-Jeweled Lotus and Mana Crypt ban, replaced with Jeweled Amulet and Everflowing Chalice (both 0-mana to trigger pingers); the deck adapted well.
4. **Not a wheels deck** — Despite Kederekt Parasite synergy with wheels, card advantage is sufficient without wheels package.
5. **Speed matters**: Ob Nixilis at 4 MV is fast for a combo commander; wants to resolve him before opponents set up.

---

## Synergy Gaps vs. Current Pipeline

| Gap | Description | Example cards |
|---|---|---|
| "Exactly 1 damage" trigger | Ob Nixilis's ability triggers on exactly 1 life lost per opponent; distinct from general damage triggers | Kessig Flamebreather, Reckless Fireweaver, Hedron Detonator (producers); Ob Nixilis (consumer) |
| 0-mana artifact as pinger fuel | Zero-cost artifacts trigger artifact ETB pingers at negligible mana cost; synergy chain not captured | Jeweled Amulet, Everflowing Chalice, Mox Amber (producers); Reckless Fireweaver, Glaring Fleshraker (consumers) |
| Non-creature spell pingers | Cards that deal 1 damage when non-creature spells are cast (not just instants/sorceries — includes artifacts/enchantments) | Black Mage's Rod, Vivi's Persistence, Kessig Flamebreather |
| All Will Be One combo | +1/+1 counter placed → deal damage to opponent; creates infinite loop with any counter source | All Will Be One (producer of ping from counter placement) |
| Impulse draw stack | Commander providing exile-play draw per trigger; chaining multiple triggers in a turn exploits this | Ob Nixilis (producer), Prosper Tome-Bound (supplemental exile draw) |
