# Ezuri, Claw of Progress — Primer Analysis

**Commander:** Ezuri, Claw of Progress {2}{G}{U} — 2/2 Legendary Creature (Elf Warrior)
**Color identity:** Simic (GU)
**Source:** Moxfield primer by unknown author
**Theme:** Experience counters via low-power ETBs; buff one creature per combat

---

## Commander Ability (the trigger)

> Whenever a creature with power 2 or less enters the battlefield under your control, you get an experience counter.
> At the beginning of combat on your turn, target creature you control gets +X/+X until end of turn, where X is the number of experience counters you have.

Key constraints:
- **Power ≤ 2** at ETB time — restricts creature selection but rewards budget creatures
- **One creature gets the buff per combat** — dictates that the deck needs an evasive finisher to dump all experience into
- Experience counters are **permanent** — Ezuri doesn't need to stick to win, counters carry over even if he's recast

---

## Primary Synergy Packages (by the primer author's own labels)

### 1. Power ≤ 2 creature density
The deck is built around creatures with power ≤ 2 that generate value on ETB. This intentionally excludes most generic Simic goodstuff (large hydras, etc.) and instead leans into:
- 1-drop dorks: Elvish Mystic, Llanowar Elves, Fyndhorn Elves, Birds of Paradise
- ETB value creatures: Baleful Strix, Mulldrifter (evoke), Coiling Oracle, Reclamation Sage
- Token producers (each token = another experience counter trigger): Avenger of Zendikar, Verdant Force
- Infinite-ETB enablers: Sage of Hours, Brooding Saurian, Sage of Hours

**Model insight:** Power ≤ 2 is a structural constraint that shapes the entire creature composition. Creatures good in Ezuri are not the same as creatures good in generic Simic.

### 2. Experience counter accumulation
The experience engine scales exponentially when mass ETBs occur:
- Creature-based blink/bounce: Conjurer's Closet, Temur Sabertooth, Displace
- Token generation: Avenger of Zendikar fills the board with 0/1 plants (all ≤ 2 power)
- Elfball dorks: The Elf sub-theme provides mass ETBs when elves are played in sequence

**Model insight:** ETB-trigger density is the primary metric, not mana cost or creature quality. Cards that produce multiple tokens (Avenger, Verdant Force) multiply experience gains.

### 3. Combat finisher selection
Ezuri's buff targets one creature. The ideal target has:
- High native toughness or resilience
- Evasion (flying, unblockable, shroud/hexproof)
- Trample preferred to push through blockers
- Notable finishers: Chasm Skulker (draws + makes tokens on death), Sage of Hours (infinite turns), Blighted Agent (infect, wins with 10 experience counters)

**Model insight:** Infect finishers (Blighted Agent, Tuskguard Captain-enabled creatures) are extremely powerful with Ezuri since commander damage / power thresholds are halved.

### 4. Combo lines
**Sage of Hours + experience counters:**
- Requirements: Sage of Hours on battlefield, ≥ 5 experience counters
- Loop: Buff Sage of Hours with +5/+5 at combat, attack, remove 5 counters for extra turn
- With infinite ETBs (e.g. Temur Sabertooth + cheap creatures), experience can be accumulated at instant speed

**Model insight:** Extra-turn loops from combat triggers are unusual — most extra-turn cards are sorcery-speed or non-combat.

---

## Combo Lines

### Line 1: Sage of Hours infinite turns
- Requirements: Ezuri on field, Sage of Hours on field, ≥ 5 experience counters at start of combat
- Loop: Give Sage +5/+5 → attack and remove 5 +1/+1 counters → take extra turn → repeat
- Accelerant: Each ETB in an extra turn adds more experience, expanding the buffer

### Line 2: Temur Sabertooth bounce loops
- Requirements: Temur Sabertooth + any low-power creature with strong ETB + enough mana
- Loop: Pay {1}{G} to bounce creature back to hand, recast → gains ETB and experience counter
- Combined with Selvala, Heart of the Wilds (draws + produces mana per large creature) this can be mana positive

### Line 3: Avenger of Zendikar + any ETB doubler
- Avenger makes X 0/1 plants on ETB (X = lands) → each plant = experience counter
- With Conjurer's Closet, blink Avenger each end step for mass experience

---

## Deckbuilding Principles Stated by Author

1. **Power ≤ 2 as the lens** — Every creature decision filters through this; excludes most generic goodstuff.
2. **ETBs over raw power** — Creatures are chosen for what they do when they arrive, not their combat stats.
3. **One evasive finisher wins the game** — Concentrate all experience into one creature (usually infect or flying) to close.
4. **Experience is permanent** — Even suboptimal early experience counters compound into game-winning late buffs.
5. **Token synergy is essential** — Mass token producers like Avenger of Zendikar are the key experience accelerants.

---

## Synergy Gaps vs. Current Pipeline

| Gap | Description | Example cards |
|---|---|---|
| Experience counter mechanic | Entirely commander-specific mechanic; no TRIGGER_PATTERNS entry for "experience counter" gains | Ezuri, Claw of Progress; Meren of Clan Nel Toth; Daxos the Returned |
| Power ≤ 2 filtering | Cards that care about low-power creatures entering; distinct from generic ETB triggers | Ezuri (producer), all ≤2 power creatures (consumers) |
| ETB-value creature weighting | Creatures with strong ETBs should score higher for ETB-payoff commanders | Mulldrifter, Reclamation Sage, Coiling Oracle |
| Infect as alternate win condition | Infect requires only 10 poison counters; unrelated to power/toughness synergy | Blighted Agent, Tuskguard Captain |
| Extra-turn loops via combat triggers | Sage of Hours's extra turn is triggered by removing counters during combat; not a traditional spell-based extra turn | Sage of Hours |
