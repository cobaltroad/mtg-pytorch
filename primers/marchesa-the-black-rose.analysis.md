# Marchesa, the Black Rose — Primer Analysis

**Commander:** Marchesa, the Black Rose {1}{U}{B}{R} — 3/3 Human Wizard, Dethrone
**Color identity:** Grixis (UBR)
**Source:** Moxfield primer by Lokotor (last updated 2026-03-13)
**Theme:** Sacrifice/recursion engine via Dethrone; aristocrats attrition; mid-power (75%)

---

## Commander Ability (the trigger)

> Dethrone (Whenever this creature attacks the player with the most life or tied for most life, put a +1/+1 counter on it.)
> Other creatures you control have Dethrone.
> Whenever a creature you control with a +1/+1 counter on it dies, return that card to the battlefield under your control at the beginning of the next end step.

Key constraints:
- **Dethrone** only triggers when attacking the highest-life player — requires staying behind on life or attacking the leader
- **End-step recursion**: Creatures return at end step, not immediately; timing matters for instant-speed sacrifice
- **Marchesa needs a counter to self-recurse** — she must attack or get a counter from another source to protect herself
- **Exile beats her** — Rest in Peace and repeated exile effects are the hard counters

---

## Primary Synergy Packages (by the primer author's own labels)

### 1. Sacrifice outlets (Altars)
All sacrifice outlets should be **free and instant-speed** to maximize Marchesa's recursion on opponent's turns:
- **Mana-generating altars**: Phyrexian Altar (colored mana), Ashnod's Altar (colorless mana)
- **Scrying**: Viscera Seer (top-deck control)
- **Self-feeding**: Carrion Feeder (gains counters on sacrifice), Yahenni (gains counters when anything dies), Smothering Abomination (sac outlet + card draw)
- **Land-based**: Phyrexian Tower, High Market (harder to remove, one creature per turn)

**Model insight:** Sacrifice outlets that produce mana (Phyrexian Altar, Ashnod's Altar) are central combo enablers, not just outlets. They convert creatures into mana and are producers for multiple different consumer patterns.

### 2. Creatures with self-generating +1/+1 counters (Lambs)
Creatures that arrive with or quickly gain their own +1/+1 counter — they can be safely sacrificed without needing a Dethrone trigger:
- **Artifact creatures with counters**: Arcbound Worker, Iron Apprentice (also pump other artifact creatures on death)
- **Self-growing sacrificers**: Carrion Feeder, Yahenni, Undying Partisan
- **Graft/modular mechanics**: Cytoplast Manipulator (can graft to other creatures), Vigean Graftmage
- **Connive (Lethal Scheme)**: Gains counters while filtering cards — doubles as free-ish removal

### 3. ETB/Dies value engine
Recurring ETB and death triggers is the value engine:
- **Card draw on death/ETB**: Baleful Strix (draw on ETB, recurring), Grim Haruspex (draw when non-token creature dies), Sage of Fables (remove excess counters for cards)
- **Removal on ETB**: Skinrender (-3/-3 on ETB, recurrable for repeated debuffs), Ravenous Chupacabra, Plaguecrafter (force sacrifice, recur for attrition)
- **Ramp on ETB**: Solemn Simulacrum (draw + land, recurrable)

**Model insight:** "Recurrable ETB creatures" are a distinct category: they must have both a strong ETB effect AND the ability to gain +1/+1 counters (from Dethrone or another source) to be safe to sacrifice.

### 4. Life total manipulation
Dethrone requires being below the leader's life total — tools to lower own life:
- **Pain lands**: Underground River, Sulfurous Springs, City of Brass (controllable life loss)
- **Necropotence**: Spend life for cards (also happens to lower your total)
- **Toxic Deluge**: Board wipe that costs life (doubles as life total manipulator)
- **Unspeakable Symbol**: Pay 3 life for a +1/+1 counter at instant speed (removes need for Dethrone)

**Model insight:** "Pay life for card advantage" effects (Necropotence, Toxic Deluge) have dual utility in Marchesa: they provide value AND ensure Dethrone remains active by keeping your life total below opponents.

### 5. Board control (Grave Pact effects)
Once the recursion engine is running, sacrifice-based board control locks opponents out:
- **Grave Pact, Dictate of Erebos**: Each sacrifice forces opponents to sacrifice too
- **Counterspells (recurring)**: Voidmage Prodigy, Glen Elendra Archmage — recurrable counterspells via sacrifice + Marchesa

---

## Combo Lines

### Line 1: Marchesa + free sacrifice outlet + Plaguecrafter
- Requirements: Marchesa with counter, Plaguecrafter on field with counter, any free sacrifice outlet
- Loop: Sacrifice Plaguecrafter → it returns at end of turn → next turn, sacrifice and return → each loop forces opponents to sacrifice a creature or discard
- Result: Soft lock on opponent's creature deployment

### Line 2: Marchesa + Solemn Simulacrum loop
- Requirements: Marchesa with counter, Solemn Simulacrum, Ashnod's Altar or other free outlet
- Loop: Sacrifice Sim → gains {2} + opponent draws life trigger resolves → Sim returns at end step → repeat each turn
- Result: One land per turn, one draw per turn for free

### Line 3: Marchesa + Baleful Strix loop
- Requirements: Marchesa with counter, Baleful Strix, free sacrifice outlet
- Loop: Sacrifice Strix → draw a card → Strix returns at end step → repeat each turn
- Result: One card draw per turn from a 2-mana investment

---

## Deckbuilding Principles Stated by Author

1. **Sacrifice outlets must be instant-speed and free** — Allows sacrificing on opponent's end step so creatures return before your turn.
2. **~25% of creatures should self-generate +1/+1 counters** — Ensures reliable sacrifice fodder without depending on Dethrone.
3. **Grixis ramp is artifact-only** — CMC ≤ 2 rocks are prioritized (Talisman cycle, Fellwar Stone).
4. **Grave Pact effects create an inevitable lock** — Once running, opponents can't maintain a board.
5. **Avoid 7-mana cards** — The author repeatedly cuts expensive cards; curve discipline is essential.

---

## Synergy Gaps vs. Current Pipeline

| Gap | Description | Example cards |
|---|---|---|
| Dethrone mechanic | Attack-trigger that adds +1/+1 counter when attacking highest-life player; not in TRIGGER_PATTERNS | Marchesa (producer), all creatures with Dethrone |
| Recurrable ETB creatures | Creatures whose value comes from repeatedly sacrificing + returning via Marchesa recursion | Baleful Strix, Solemn Simulacrum, Plaguecrafter, Skinrender |
| Life-total manipulation as synergy | Lowering own life total intentionally to enable Dethrone; "pain lands" as synergy pieces | Underground River, Necropotence, Unspeakable Symbol |
| Instant-speed sacrifice as timing advantage | Sacrificing on opponent's end step (rather than your own) is a strategic distinction not in the model | Phyrexian Altar, Viscera Seer, Ashnod's Altar |
| Grave Pact lock | Repetitive sacrifice forcing opponents to sacrifice is an attrition stax pattern; distinct from "death trigger" | Grave Pact, Dictate of Erebos (consumers of sacrifice frequency) |
