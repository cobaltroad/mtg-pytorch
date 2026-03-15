# Hakbal of the Surging Soul — Primer Analysis

**Commander:** Hakbal of the Surging Soul {2}{G}{U} — 4/4 Merfolk Scout, Mythic
**Color identity:** Simic (GU)
**Source:** Moxfield primer by Hydrax (last updated 2026-01-10)
**Theme:** Merfolk tribal + Explore mechanic + +1/+1 counters; win via combat

---

## Commander Ability (the trigger)

> At the beginning of combat on your turn, each Merfolk creature you control Explores.
> Whenever Hakbal attacks, you may put a land card from your hand onto the battlefield. If you don't, draw a card.

Key constraints:
- Explore is a **single trigger** for all Merfolk — order matters, Roaming Throne doubles it
- Explore when revealing a **land**: must put it in hand (mandatory)
- Explore when revealing a **non-land**: choice to keep on top or put in graveyard; creature gets +1/+1 counter regardless
- **Land deployment vs. draw** on attack is a strategic choice that shifts with game phase

---

## Primary Synergy Packages (by the primer author's own labels)

### 1. Merfolk Tribal / Explore density
Wider board = more Explore triggers each combat. Key cards:
- **Merfolk lords**: Lord of Atlantis, Master of the Pearl Trident, Merfolk Sovereign (grant Islandwalk)
- **Explore doublers**: Topography Tracker, Roaming Throne (each Merfolk Explores twice)
- **Token generators**: Deeproot Waters, Deeproot Pilgrimage (each non-token Merfolk creates a token)

**Model insight:** Merfolk type_line tribal synergy is exactly what `compute_tribal_typeline_synergy()` covers. Hakbal's Explore trigger is additional value on top of the tribal engine.

### 2. +1/+1 Counter payoffs
Every Explore on a non-land result gives a +1/+1 counter. Payoffs:
- **Unblockability**: Herald of Secret Streams (all creatures with counters unblockable)
- **Trample**: Zegana, Utopian Speaker, Sphere Grid
- **Stat amplifiers**: Deeproot Elite, Hardened Scales, Kumena Tyrant of Orazca
- **Card draw from countered creatures**: Bred for the Hunt, Benthic Biomancer (mandatory loot)

**Model insight:** Standard +1/+1 counter payoffs apply here, but with a tribal constraint — the counter source is exploration, not direct placement.

### 3. Top-deck manipulation
Explore quality depends on knowing what's on top of the library:
- **Scry effects**: Path of Ancestry gives scry for tribal casting
- **Realmwalker, Emperor Mihail II**: Let you see/cast Merfolk from the top
- **Explore order optimization**: With knowledge of top card, you choose which Merfolk Explores first (guarantees counter on desired target)

**Model insight:** Top-deck manipulation synergy is not captured by current pipeline. Cards that let you see the top card have special value with Explore/Revolt/etc. commanders.

### 4. Evasion / finishers
Merfolk with counters need ways through blockers:
- **Islandwalk**: Lord of Atlantis, Master of the Pearl Trident, Tide Shaper (turns a land into an Island)
- **Pure unblockability**: Herald of Secret Streams, Deepchannel Mentor, Merfolk Sovereign, Mist Dancer
- **Wanderwine Prophets**: Extra turns by sacrificing Merfolk; ends games via repeated attacks

**Model insight:** Islandwalk as evasion requires the opponent to have an Island. Tide Shaper (converts any land to Island) is the enabler that makes Islandwalk reliable.

---

## Combo Lines

### Line 1: Kiora's Follower + Deeproot Pilgrimage (Glasspool Mimic as copy)
- Requirements: Kiora's Follower on field, Glasspool Mimic or Deepfathom Echo as a copy of Kiora's Follower, Deeproot Pilgrimage on field
- Loop: Tap Kiora's Follower to untap any permanent → triggers Deeproot Pilgrimage (Merfolk token created) → activate copy to untap original Kiora's Follower → repeat
- Result: Infinite Merfolk tokens
- Limitation: Tokens lack haste (need another turn or haste-granter to win immediately)

**Key combo-piece cards:**
- Kiora's Follower — the untap engine
- Glasspool Mimic / Deepfathom Echo — copies Kiora's Follower for the second untap
- Deeproot Pilgrimage — the token generator triggered by untapping

---

## Deckbuilding Principles Stated by Author

1. **Ramp early, Hakbal ASAP** — Get a couple of Merfolk in play before casting Hakbal for immediate explore value.
2. **Be selective with Explore in mid-game** — Early game: keep everything. Mid-late: aggressively dump non-essential cards.
3. **Guarantee counters by controlling explore order** — If you know the top card, place Hakbal last/first in explore order strategically.
4. **Protect Hakbal at all costs** — Without him the deck is a mediocre tribal pile.
5. **Track commander damage** — Hakbal will be a large trampler with counters; 21 is achievable.

---

## Synergy Gaps vs. Current Pipeline

| Gap | Description | Example cards |
|---|---|---|
| Explore mechanic | "Explore" trigger (reveal top card, land → hand; non-land → graveyard or top, creature gets counter) is not in TRIGGER_PATTERNS | Hakbal, Cenote Scout, Topography Tracker |
| Islandwalk payoffs | Islandwalk enablers (convert opponent lands to Islands) + Islandwalk creatures form a sub-synergy cluster | Tide Shaper (enabler), Lord of Atlantis, Cold-Eyed Selkie (payoffs) |
| Top-deck manipulation as synergy | Cards that reveal/control top of library have elevated value for Explore commanders | Path of Ancestry, Realmwalker, Emperor Mihail II |
| Explore doubling | "Roaming Throne" doubles Hakbal's triggered ability; not a standard "+1/+1 counter doubler" | Roaming Throne, Topography Tracker |
| Extra-turn via tribal combat | Wanderwine Prophets: sacrifice Merfolk during combat to take extra turn; very different from spell-based extra turns | Wanderwine Prophets |
