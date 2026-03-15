# Ygra, Eater of All — Primer Analysis

**Commander:** Ygra, Eater of All {3}{B}{G} — 6/6 Cat Horror, Ward — sacrifice a Food
**Color identity:** Golgari (BG)
**Source:** Moxfield primer by monarchtempest (last updated 2025-12-06)
**Theme:** Food tokens + creature-as-artifact interactions enabling infinite combos; Bracket 4

---

## Commander Ability (the trigger)

> Ward — Sacrifice a Food.
> Other creatures are Food artifacts in addition to their other types and have "{2}, {T}, Sacrifice this permanent: You gain 3 life."
> Whenever a Food is put into a graveyard from the battlefield, put two +1/+1 counters on Ygra, Eater of All.

Key constraints:
- **ALL creatures become Food artifacts** (including opponents' creatures) — enables artifact-based removal AND self-synergy
- **Ward cost is sacrifice a Food** — opponents must sacrifice creatures to remove Ygra (which triggers Ygra's counter ability)
- **Sacrifice Food → 2 counters on Ygra**: Each creature death grows Ygra; with sacrifice loops, Ygra becomes arbitrarily large
- **Creatures ARE artifacts**: All artifact payoffs (Disciple of the Vault, Reckless Fireweaver, Grinding Station, Arcbound Ravager) work on creature deaths

### Critical implication: Tower of the Magistrate
Since all creatures are artifacts, activating Tower of the Magistrate (gives target artifact protection from artifacts) on Ygra makes Ygra unblockable (all potential blockers are artifacts = Food).

---

## Primary Synergy Packages (by the primer author's own labels)

### 1. Food-based infinite combos

**Ygra + Camellia, the Seedmiser + sacrifice outlet + sacrifice target**:
- Camellia: When you sacrifice a Food, create a 1/1 Squirrel token
- Sacrifice any creature (now a Food) → Camellia makes a Squirrel → sacrifice Squirrel → Camellia makes another Squirrel → infinite
- Payoffs needed: Blood Artist, Disciple of the Vault, Altar of the Brood, Marionette Apprentice, Mirkwood Bats for damage

**Ygra + Experimental Confectioner + sacrifice outlet + Ygra**:
- Confectioner ETB: Create a Food token, then if you sacrifice it this turn for 2 mana, create a 1/1 Rat
- Sacrifice the Rat (creature-Food) → Confectioner ETB again → infinite tokens

**Ygra + Animation Module + Ashnod's Altar + sacrifice target**:
- Sacrifice creature (Food) → {2} colorless from Altar → Ygra gets +1/+1 counters → Animation Module sees counter placed → pay {1} → create a 1/1 Servo (colorless artifact creature) → sacrifice Servo → net mana and repeat
- Result: Infinite colorless mana (with Ashnod's), infinite ETBs, infinite +1/+1 counters on Ygra

### 2. Artifact-creature payoffs
Because all creatures are artifacts, the following trigger on creature deaths:
- **Disciple of the Vault**: Each artifact put into graveyard → opponent loses 1 life
- **Blood Artist**: Creature death → drain 1 life
- **Marionette Apprentice**: Pay {2}: creatures you don't control get -1/-1 until end; alternatively, artifact sac → drain equal to Apprentice's power
- **Mirkwood Bats**: Food token sacrificed → each opponent loses 1 life
- **Altar of the Brood**: Each creature/artifact ETB → each opponent mills 1

**Model insight:** Ygra uniquely bridges "sacrifice creature" and "sacrifice artifact" synergy lines — all payoffs for artifact sacrifice apply to creature sacrifice and vice versa.

### 3. Tutor density (14 tutors)
The deck runs an unusually high number of tutors to find combo pieces:
- **Instant-speed**: Vampiric Tutor, Worldly Tutor
- **Sorcery-speed**: Demonic Tutor, Tainted Pact (every card different name in 99)
- **Creature tutors**: Thornvault Forager (searches squirrel-type creatures including Camellia)
- **Artifact tutors**: Inventor's Fair (tutors when ≥ 3 artifacts on field — trivially met with creatures being artifacts)
- **Branching paths**: Insatiable Avarice (draw 3 OR top-deck tutor)

### 4. Card advantage engines
- **Sylvan Library**: Top 3 each draw step; in Ygra, life loss is low-priority given the ward requirement encourages opponents not to target you
- **Necropotence**: Dig 20+ cards at once with low life concern
- **Beast Whisperer + Guardian Project**: Draw on creature ETBs (which are now artifact ETBs too)

---

## Combo Lines

### Line 1: Ygra + Camellia + Phyrexian Altar + any creature (infinite mana, infinite tokens)
- Requirements: Ygra on field, Camellia on field, Phyrexian Altar on field, one creature to start
- Loop: Sacrifice creature (Food artifact) → {B} or {G} from Altar → Ygra gets 2 counters → Camellia makes 1/1 Squirrel → sacrifice Squirrel → {B} or {G} → repeat
- Result: Infinite colored mana, infinite Squirrel ETBs, infinite +1/+1 counters on Ygra
- Win: With Blood Artist / Disciple of the Vault in play, drain opponents to 0

### Line 2: Ygra + Animation Module + Ashnod's Altar (infinite colorless mana)
- Requirements: All three on field, one creature to start
- Loop: Sacrifice creature → {2} colorless → counter on Ygra → Module trigger → pay {1} → Servo token → sacrifice Servo → {2} → counter → Module → repeat
- Net: +1 colorless per cycle (infinite)
- Win: Infinite mana → cast Walking Ballista for infinite damage OR sink into any X-spell

### Line 3: Hazel's Brewmaster + Devoted Druid (infinite mana from graveyard)
- Requirements: Devoted Druid in graveyard, Hazel's Brewmaster cast from hand
- Brewmaster ETB: Exile a card from graveyard; if Food (which Devoted Druid is since all creatures are Foods now), gain its activated abilities
- Devoted Druid's abilities: {T}: Add {G}; Pay 1 life, put -1/-1 counter: Untap Devoted Druid
- On any creature (now that creature has Devoted Druid's abilities via Brewmaster): Tap for {G}, use -1/-1 to untap, tap for {G}, repeat
- Result: Infinite {G} mana

### Line 4: Walking Ballista + Mikaeus the Unhallowed (infinite damage)
- Requirements: Mikaeus on field, Ballista in hand
- Cast Ballista for X=0 → Mikaeus grants +1/+1 → Ballista is a 1/1 → use Ballista ability to ping itself → removes counter → Ballista dies → Undying returns it with counter → repeat
- Win: Ping opponents infinitely

---

## Deckbuilding Principles Stated by Author

1. **Create dilemmas, not problems** — The best wins in a non-blue combo deck come from putting opponents in impossible positions where any response accelerates your position.
2. **Multiple combo lines keeps opponents guessing** — Ygra, Camellia, Animation Module, Brewmaster/Devoted Druid, and Ballista/Mikaeus are four different win conditions.
3. **Ygra's ward is a deterrent, not guaranteed protection** — Opponents sacrificing creatures to pay ward triggers Ygra's counter ability; ward is self-reinforcing.
4. **Tower of the Magistrate as surprise unblockable** — Since all blockers are artifacts, grant Ygra protection from artifacts at instant speed for an unexpected attack.
5. **Artifact removal IS creature removal** — Creeping Corrosion and Season of Gathering (normally one-sided artifact wipes) become one-sided creature wipes with Ygra out.

---

## Synergy Gaps vs. Current Pipeline

| Gap | Description | Example cards |
|---|---|---|
| Creature-as-artifact bridging | Ygra makes all creatures Food artifacts; all artifact sacrifice payoffs apply to creature deaths | Disciple of the Vault, Marionette Apprentice, Arcbound Ravager (consumers); Ygra (the enabler) |
| Food token loop with token generator | Camellia makes Squirrels when Food is sacrificed; creates infinite loop with sacrifice outlet | Camellia the Seedmiser, Experimental Confectioner (loop partners with Ygra) |
| Animation Module combo | Animation Module: counter placed → pay {1} → create Servo; creates infinite loop with counters + Ashnod's Altar | Animation Module (combo piece); not currently in PRODUCER_MAP |
| Ward-sacrifice as Ygra counter trigger | Ward requiring sacrifice means opponents triggering ward actually help Ygra grow; self-reinforcing protection | Ward — Sacrifice a Food (Ygra-specific) |
| Inventor's Fair artifact tutor | Tutors when ≥ 3 artifacts on field; trivially met in Ygra since all creatures are artifacts | Inventor's Fair (tutor enabled by Ygra's blanket creature-to-artifact conversion) |
