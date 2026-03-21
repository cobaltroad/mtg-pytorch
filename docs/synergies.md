# MTG Synergy Edge Definitions

Source of truth: `services/ingest/pipeline.py` — `TRIGGER_PATTERNS` and `PRODUCER_MAP`.

Each synergy edge connects a **producer** card (generates an event) to a **consumer** card (reacts to it). Edges are stored in the `synergy_edges` table with `score_type = 'ability_trigger'`.

Edges are capped at `SYNERGY_LIMIT` (default 500 000) per trigger event to prevent disk fill. Override with the `SYNERGY_LIMIT` env var.

To regenerate edges after changing patterns, re-run:
```
docker compose run --rm ingest python pipeline.py --stage tag_abilities
docker compose run --rm ingest python pipeline.py --stage compute_synergy
docker compose run --rm ingest python pipeline.py --stage compute_tribal_typeline_synergy
```

The third stage runs independently and can be re-run alone whenever tribe lists change.

---

## How the model works

```
TRIGGER_PATTERNS   →  card_abilities table  (consumers, tagged by trigger_event)
PRODUCER_MAP       →  runtime SQL WHERE     (producers, matched at compute time)

synergy_edges = producers × consumers sharing the same trigger_event
```

Consumer regex patterns are applied case-insensitively to `oracle_text` during `tag_abilities`. Producer SQL WHERE clauses are evaluated against `oracle_text` and `type_line` during `compute_synergy`.

---

## ETB (Enters the Battlefield) — hierarchy

Patterns are evaluated **most-specific first**. A card tagged `nontoken_etb` will not also be tagged `creature_etb` for the same match.

| Event | Consumer regex matches | Producer SQL matches |
|---|---|---|
| `nontoken_etb` | "whenever a nontoken creature enters the battlefield" | Reanimation spells (creature from GY to BF), library cheaters (Collected Company, Green Sun's Zenith), blink effects (exile target → return to BF) |
| `creature_etb` | "whenever a creature enters the battlefield" | Token creators (create a/two/three), reanimation, library cheaters, blink |
| `artifact_etb` | "whenever an artifact enters the battlefield" | Treasure/food/clue/gold token creators, `put % artifact % battlefield` |
| `enters_battlefield` | "whenever ~ enters the battlefield" (catch-all) | Token creators, `put onto the battlefield`, reanimation, blink |

**Tuning notes:**
- `nontoken_etb` is the Aristocrats-adjacent / reanimator ETB event. Blinking a token does not trigger it.
- `creature_etb` is for mass-ETB commanders like Yarok or Panharmonicon decks.
- `artifact_etb` is intentionally broad; artifact creatures entering will fire it even though few consumer cards specifically say "whenever an artifact enters" — most prefer `artifact_matters`.

---

## Creature Death

| Event | Consumer regex | Producer SQL |
|---|---|---|
| `nontoken_dies` | "whenever a nontoken creature dies" | Sacrifice outlets, destroy target creature, destroy all creatures, mass wipes |
| `dies` | "whenever ~ dies" (any creature) | Sacrifice outlets, destroy target/all, "deals damage" sources |

**Tuning notes:**
- `nontoken_dies` is the Aristocrats payoff event (Blood Artist, Zulaport Cutthroat). Token deaths don't trigger it.
- `dies` is broader and will produce noisy edges; consider raising `nontoken_dies` priority or restricting `dies` producer further.

---

## Combat

| Event | Consumer regex | Producer SQL |
|---|---|---|
| `attacks` | "whenever ~ attacks" | Haste granters, "must attack", "attacks each combat/turn" |
| `combat_damage` | "whenever ~ deals (combat) damage to a player/opponent/you" | Evasion (can't be blocked), double strike, trample, menace, "deals combat damage" |
| `phase_begin` | "at the beginning of (your/each) upkeep/end step" | "at the beginning of", "during your upkeep", "each upkeep" |

---

## Spellslinger (`spell_cast`)

Both consumer patterns map to the **same** `spell_cast` trigger event.

| Consumer pattern | Example cards |
|---|---|
| "whenever you/a player/an opponent cast(s) (noncreature\|instant or sorcery\|a) spell" | Goblin Electromancer, Thousand-Year Storm |
| `magecraft` keyword | Archmage Emeritus, Clever Lumimancer |

**Producer SQL:** `type_line` contains `instant` or `sorcery`; also storm, cascade, flashback, "cast another", "cast an additional" effects.

**Tuning notes:**
- Storm and cascade are included as producers because they generate additional casts.
- "cast a spell" is intentionally broad — includes creature spells, which may add noise for narrow spellslinger commanders. Consider a `noncreature_spell_cast` event if precision is needed.

---

## Lifegain (`lifegain`)

| Consumer regex | Producer SQL |
|---|---|
| "whenever you/a player gain(s) life" | `you gain % life`, `gain life`, `gains life`, `lifelink`, `life equal to` |

**Cross-synergy:** Angel tribal producers (`tribal_angel_*`) are extended to include lifegain producers (angels + lifegain spells share producer pool).

---

## Lifegain Threshold (`lifegain_threshold`)

End-step payoffs that check **cumulative life gained this turn** rather than reacting to individual gain events.

| Consumer regex | Producer SQL |
|---|---|
| "if you gained \d+ or more life this turn" | `you gain % life`, `gain life`, `gains life`, `lifelink`, `life equal to`, `create % food %`, `food token` |

**Example consumers:** Resplendent Angel ("if you gained 5 or more life this turn, create a 4/4 Angel"), Angelic Accord ("if you gained 4 or more life this turn, create a 4/4 Angel"), Valkyrie Harbinger, Dawn of Hope.

**Producer pool:** All direct lifegain sources (same as `lifegain`) **plus Food token creators**. Each Food token has the intrinsic ability "Sacrifice this artifact: You gain 3 life." — it is the *token* that gains the life, not the creator card directly. Creator cards are matched because the tokens they produce are what ultimately enable the 3-life gain. Two Food tokens clear every standard threshold (4 for Angelic Accord / Valkyrie Harbinger, 5 for Resplendent Angel); a single Food already satisfies any "if you gained 3 or more life" payoff. Food creators (Gilded Goose, Trail of Crumbs, etc.) are therefore first-class producers for this event even though they do not gain life directly.

**Lifegain cascade chain:** The real power of `lifegain_threshold` is a multi-step feedback loop driven by token generators, Soul Sisters, and Angel-ETB payoffs:

```
[token generator]       --creature_etb-->   Soul Warden / Soul's Attendant
                                              (creature_etb consumer → gains 1 life per ETB)

Soul Warden             --lifegain_threshold-->  Angelic Accord / Resplendent Angel
                                              (lifegain_threshold consumer → creates Angel token)

[Angel token ETB]       --tribal_angel_etb-->  Bishop of Wings / Righteous Valkyrie
                                              (tribal_angel_etb consumer → gains 2 life)

Bishop of Wings         --lifegain_threshold-->  Angelic Accord / Resplendent Angel
                                              (loop: more Angels → more life → more Angels)
```

- **Soul Warden / Soul's Attendant**: tagged as `creature_etb` **consumers** ("Whenever another creature enters the battlefield, you gain 1 life") and `lifegain` **producers** (the gained life accumulates toward the threshold). They pair directly with a deck's own token-generating abilities — every token that enters triggers a lifegain event.
- **Angelic Accord / Resplendent Angel**: tagged as `lifegain_threshold` **consumers**. When the cumulative threshold is met at end step, they create Angel tokens with lifelink, re-feeding the loop.
- **Bishop of Wings / Righteous Valkyrie**: tagged as `tribal_angel_etb` **consumers** and `lifegain` **producers** — each Angel token entering triggers 2 more life, which can re-satisfy the threshold for another token next turn.

**Tuning notes:** The threshold condition is evaluated at the end step; the consumer pattern fires at tagging time regardless of the actual cumulative total. This is intentional — whether a deck regularly gains 4+ life per turn is a deck-construction question, not an individual-card question.

---

## Lifegain Replacement (`lifegain_replacement`)

Replacement effects that intercept a lifegain event and modify its amount — distinct from triggered responses (which fire *after* the gain) and threshold checks (which fire *once per turn at end step*).

| Consumer regex | Producer SQL |
|---|---|
| "if you would gain life" | `you gain % life`, `gain life`, `gains life`, `lifelink`, `life equal to` |

**Example consumers:** Angel of Vitality ("If you would gain life, you gain that much life plus 1 instead"), Boon Reflection ("If you would gain life, you gain twice that much life instead"), Rhox Faithmender (same doubling template).

**Producer pool:** Any direct lifegain source — the replacement fires whenever *any* life is gained, so all `_LIFEGAIN_PRODUCER_SQL` cards are valid producers.

---

## Life Total Threshold (`lifegain_total`)

Static or end-step abilities that check your **current life total** (rather than life gained since last turn) against an absolute number or your starting life total.

| Consumer regex | Producer SQL |
|---|---|
| "if you have \d+ or more life" / "more than your starting life total" / "greater than your starting life total" | `you gain % life`, `gain life`, `gains life`, `lifelink`, `life equal to` |

**Example consumers:**
- Serra Ascendant ("As long as you have 30 or more life, Serra Ascendant gets +5/+5 and has flying.")
- Angel of Destiny ("At the beginning of each end step, if your life total is greater than your starting life total, each opponent with life equal to or less than that much life loses the game.")

**Producer pool:** Any direct lifegain source that raises the life total — same as `lifegain`. The higher your total, the more likely a threshold or comparison resolves in your favour.

---

## Landfall (`landfall`)

| Consumer regex | Producer SQL |
|---|---|
| "whenever a land enters" | Fetch lands (search library for % land), `put a basic/a land % battlefield`, "play an additional land", "land card onto the battlefield" |

---

## Discard (`discard`)

| Consumer regex | Producer SQL |
|---|---|
| "whenever you/a player/an opponent discard(s)" | `discard a card`, `discard your hand`, `each player discards`, `target player discards`, `discard two`, "draw a card, then discard" |

**Tuning notes:** Wheels (Wheel of Fortune) and looting effects (Faithless Looting) are both captured. If Nekusar-style group-discard needs its own event, split into `self_discard` and `opponent_discard`.

---

## Token Creation (`token_creation`)

| Consumer regex | Producer SQL |
|---|---|
| "whenever you create (a/X/one or more) token" | `create a/two/three/x % token`, `put a % token % onto the battlefield` |

---

## Counter Added (`counter_added`)

Generic counter trigger — any counter type on any permanent.

| Consumer regex | Producer SQL |
|---|---|
| "whenever ~ (counter/counters) (placed/put) on" | proliferate, `put a +1/+1 counter`, `+1/+1 counter on each`, `put a counter on`, double counters |

**See also:** `plus_one_counters` for the more specific +1/+1 placement/doubling theme.

---

## Combat Damage (`combat_damage`)

| Consumer regex | Producer SQL |
|---|---|
| "whenever ~ deals (combat) damage to a player/opponent/you" | Can't be blocked, double strike, trample, menace, "deals combat damage" |

---

## Sacrifice (`sacrifice`)

| Consumer regex | Producer SQL |
|---|---|
| "whenever you sacrifice" | `sacrifice a creature`, `sacrifice another`, `sacrifice a permanent`, `sacrifice target`, `sacrifice:` (activated cost) |

---

## Deckbuilding Themes

### Skullclamp Target (`skullclamp_target`)

**Why this exists:** Skullclamp's optimal play pattern — equip to a 1-toughness token, the token immediately becomes a 0-toughness creature and dies, drawing 2 cards for {1} — is an *emergent* synergy between Skullclamp and token generators. Only the toughness matters: a 0/1 Plant token, a 2/1 Goblin token, or a 3/1 Elemental token all die just as cleanly as a 1/1 Soldier. No direct regex on either card's oracle text can capture this: Skullclamp doesn't mention tokens, and token generators don't mention Skullclamp. A direct ability-trigger match would never link the two.

This event is a **proxy synergy edge**: Skullclamp is tagged as a consumer via its toughness-drain text (`equipped creature gets +\S+/-1`), and any 1-toughness token producers are tagged as producers. The result is a synergy edge from each such token generator to Skullclamp.

**Consumer regex:** `equipped creature gets \+\S+/-1` — matches Skullclamp's characteristic "+1/-1" static bonus on the equipped creature, which reduces any 1-toughness creature to 0 toughness.

**Producer SQL:** Cards that explicitly create or put X/1 creature tokens onto the battlefield (any power, toughness 1):
- `create%/1 %token%` / `creates%/1 %token%` (Raise the Alarm, Ophiomancer, Bitterblossom, Monastery Mentor, Endrek Sahr, …)
- `put a%/1 %token%` (Lingering Souls, Adeline, Rhys the Redeemed, …)
- `put%/1%creature token%` (broader put-onto-battlefield phrasing)

This captures 0/1 Plants/Saprolings, 1/1 Soldiers/Spirits, 2/1 Goblins, 3/1 Elementals, and any other token with toughness 1.

**Cross-references:** [Issue #2](https://github.com/cobaltroad/mtg-pytorch/issues/2) · [TODO.md — Skullclamp: Emergent / Indirect Synergy](TODO.md)

**Tuning notes:** Skullclamp is colorless (`color_identity = '{}'`), so the existing `OR pc.color_identity = '{}'` branch in `compute_synergy()` ensures its edges link correctly to any colored 1-toughness token producer without special-casing.

---

### Equipment Matters (`equipment_matters`)

**Consumer regex matches:**
- `equipped creature (gets/gains/has/deals)` — static/triggered payoffs
- `whenever (an) equipment enters` — ETB payoffs (Puresteel Paladin)
- `creatures equipped (get/have/gain)` — anthem-on-equipped
- `equip (costs/abilities) (less/reduced/{0})` — cost reducers
- `equip {0}` — free equip
- `enters the battlefield attached to` — Living Weapon, auto-attach
- `attach ~ to target creature` — explicit attach effects

**Producer SQL:** Equipment type_line + equip cost reducers (`equip costs % less`, `equip {0}`, `equip abilities % less`) + auto-attachers (`enters the battlefield attached`, `attach target equipment`, `attach it to target creature`)

**Tuning notes:** Cost reducers like Stoneforge Mystic and Puresteel Paladin are producers here even though they're not Equipment cards. Consider whether to split into `equipment_payoff` (payoff cards) vs `equipment_support` (tutors/reducers).

---

### Legendary / Historic Matters (`legendary_matters`)

**Consumer regex matches:**
- `whenever you cast a (legendary/historic)` — cast triggers
- `legendary (creatures/permanents/spells) (get/gain/have)` — anthem effects
- `historic (spell/permanent/card)` — historic payoffs
- `you cast a historic` — Dominaria-era phrasing

**Producer SQL:** Any `legendary` type_line + any `artifact` type_line (artifacts are historic) + saga enchantments (sagas are historic)

**Tuning notes:** The artifact-as-historic inclusion makes this producer very broad. If the historic connection is too noisy, move artifacts out and give them `artifact_matters` only.

---

### Reanimator / Graveyard Return (`graveyard_return`)

**Consumer regex matches:**
- `you may (cast/activate) ~ from your graveyard` — Flashback, Escape, Jump-start
- `activate ~ only from graveyard` — Unearth-style activated abilities
- `whenever ~ returns from your/a/the graveyard` — return triggers
- `from your/a graveyard (to the battlefield/to play)` — destination clause

**Producer SQL:**
- Classic reanimation: "return target % creature % graveyard % battlefield", "creature card from % graveyard % battlefield", "put target % creature % graveyard % battlefield"
- GY-activated keywords: unearth, escape, flashback, "you may cast % from your graveyard", "activate % only from % graveyard"
- Mill: fills the graveyard making reanimation viable

**Cross-synergy:** Zombie tribal producers (`tribal_zombie_*`) include reanimation SQL so zombie cards and reanimation spells share the zombie-related edge pools.

**Tuning notes:** Escape cards (Uro, Kroxa) are both producers (they escape from GY) and consumers (they want things in the GY). This is intentional — they generate self-synergy signals. "is reanimated" was intentionally excluded as it rarely appears in oracle text.

---

### Graveyard Fill (`graveyard_fill`)

**Consumer regex matches:**
- `threshold` keyword — 7+ cards in GY
- `delirium` keyword — 4+ card types in GY
- `morbid` keyword — creature died this turn
- `whenever (a/any) card (put into/enters) % graveyard` — direct GY-fill payoffs

**Producer SQL:** mill, `put the top % card % graveyard`, surveil, dredge, "draw a card, then discard", "discard a card % draw", "each player discards"

**Tuning notes:** Morbid cares about death, not mill — its presence here may create false positives. Consider a separate `morbid` event if precision matters. Looting (draw-then-discard) is included because it self-mills the discard.

---

### +1/+1 Counters (`plus_one_counters`)

**Consumer regex matches:**
- `whenever ~ (gets/receives/is given/put) (a/one or more) +1/+1 counter` — placement triggers
- `if (one or more) +1/+1 counter would be (placed/put)` — replacement effects (Hardened Scales, Doubling Season)
- `(one/an) additional +1/+1 counter` — "put one additional" replacement phrasing
- `double ~ (number of) (counter/+1)` — counter doublers

**Producer SQL:** put a/two/x +1/+1 counter, +1/+1 counter on each, proliferate, double the number of counters, twice the number of % counter, replacement effect phrasing

**Tuning notes:** Counter doublers (Doubling Season, Unbound Flourishing) are both producers and consumers — they interact with everything in this cluster.

---

### Artifacts Matter (`artifact_matters`)

**Consumer regex matches:**
- `whenever you cast an artifact spell`
- `whenever (a/an) artifact (enters/is created)`
- `artifacts you control (get/gain/have)` — anthem effects
- `artifact (creatures/tokens) you control`

**Producer SQL:** Any `artifact` type_line (covers equipment, vehicles, artifact creatures) + token creators for: treasure, food, blood, clue, junk, mutagen

**Tuning notes:** Equipment and vehicles are both artifacts, so `artifact_matters` edges overlap with `equipment_matters`. This is intentional — an equipment deck benefits from both artifact-matter cards and equipment-specific payoffs. Blood and mutagen tokens are less common but included for Innistrad and Bloomburrow archetypes.

---

### Modified (`modified`)

Super-type covering **counters + auras + equipment** as a unified "modified creature" theme.

**Consumer regex matches:**
- `modified` keyword (explicit)
- `creatures ~ (counter/aura/equip) ~ (get/gain/have/are/attached)` — Neon Dynasty phrasing
- `auras and equipment` — explicit combined reference
- `(enchantment/aura) (and/or) (equipment/artifact)` — Kamigawa generalized phrasing

**Producer SQL:** Equipment type_line + creature auras (enchantment type_line + "enchant creature") + `put a +1/+1 counter` + proliferate + `attach % equipment`

**Tuning notes:** `modified` is intentionally a superset of `aura_matters` and `equipment_matters`. Cards that say "whenever a modified creature" will pair with producers from all three sub-themes. If the signal is too diffuse, consider restricting the producer to just equipment + auras (dropping counter sources).

---

### Aura Matters (`aura_matters`)

**Consumer regex matches:**
- `enchanted creature (gets/gains/has/deals)` — aura payoffs on the creature
- `whenever (an) (aura/enchantment) (enters/you cast/attaches)` — enchantress triggers
- `auras you control (get/give/have)` — anthem on auras
- `when ~ becomes enchanted` — on-enchant triggers
- `whenever you cast an enchantment` — broader enchantress (Sythis, Enchantress's Presence)
- `enchantments you control (get/gain/have)` — static enchantress anthems

**Producer SQL:** Aura cards (`enchantment` type_line + "enchant creature") + enchantress effects ("whenever (an) enchantment enters", "whenever you cast an enchantment", "enchantments you control", "number of enchantments") + aura tutors ("search library for % aura/enchantment", "return % aura % from % graveyard") + auto-attach auras (`enchantment` type_line + "enters the battlefield attached")

**Tuning notes:** Enchantress cards (Sythis, Eidolon of Blossoms, Argothian Enchantress) are producers here — they generate card draw whenever enchantments enter, making them synergistic with any aura or enchantment. This creates a large producer pool; the `SYNERGY_LIMIT` cap is important here.

---

### Proliferate Matters (`proliferate_matters`)

**Consumer regex matches (things that want proliferation):**
- `infect` keyword — puts poison counters on players
- `toxic` keyword — newer poison counter mechanic
- `wither` keyword — puts -1/-1 counters instead of damage
- `-1/-1 counter` — wither/infect payoffs
- `poison` — poison counter references
- `whenever ~ planeswalker ~ enters` — loyalty counter payoffs

**Producer SQL (things that proliferate or generate proliferate-worthy counters):** proliferate, infect, toxic, wither, `poison counter`, `-1/-1 counter`, planeswalker type_line

**Tuning notes:** Planeswalker type_line is included as a producer because loyalty counters are the most common proliferate target outside of infect. If the planeswalker producer creates too much noise, restrict to `oracle_text LIKE '%proliferate%'` and infect/toxic/wither only. -1/-1 counters and poison counters deliberately overlap — both are degradation strategies that benefit from proliferate.

---

### Play from Exile / Cascade Payoffs (`play_from_exile`)

Covers cards that reward or react to spells being cast from exile, including impulse-draw payoffs, cascade/discover payoffs, the *paradox* keyword introduced in the Dr Who set, and indirect cascade payoffs that scale with the number of spells cast or creatures that entered this turn.

**Consumer regex matches:**
- `whenever (you) cast ~ from exile` — cast-from-exile triggers (Laelia the Blade Reforged, Birgi/Harnfel back face)
- `whenever (you) cast ~ exiled (this way|with X)` — Laelia's alternate phrasing
- `paradox` keyword — The Thirteenth Doctor and other Dr Who cards; "whenever you cast a spell from anywhere other than your hand"
- `whenever (you) cast ~ from anywhere other than your hand` — explicit paradox wording
- `whenever (you) cast (a spell with cascade | a cascading spell)` — cascade payoffs (Faldorn Dread Wolf Herald, Abaddon the Despoiler)
- `whenever (you) cast ~ with cascade` — broader cascade trigger phrasing
- `number of (instant|sorcery|spells) ~ cast this turn` — indirect cascade payoffs that scale with spell count (Noise Marine)
- `creatures ~ entered the battlefield this turn` — indirect cascade payoffs that count ETBs triggered by cascade chains (Let The Galaxy Burn)

**Producer SQL:**
- Impulse draw — exile top of library with a timed "you may play/cast" window (Light Up the Stage, Commune with Lava, Rocco Street Chef, Jeska's Will)
  - `exile the top % you may % play/cast`
  - `exile % top % card % you may % play/cast`
  - `exile % you may play/cast % this turn` / `until`
- `cascade` keyword — matched via `'Cascade' = ANY(keywords)` for precision (avoids flavor text false positives)
- `discover` keyword — matched via `'Discover' = ANY(keywords)` for precision
- `airbend` keyword (TLA set) — matched via `'Airbend' = ANY(keywords)`; exiles a card or permanent from the battlefield/stack, owner may cast it while exiled
- Hand-exile with optional recast — `exile % from % hand % may cast` (Elite Spellbinder style)

**Tuning notes:**
- Cascade is already included in the `spell_cast` producer pool (it generates an extra cast). Its presence here as a `play_from_exile` producer creates a second, complementary edge class: cascade cards pair with cards that specifically reward casting *from exile* (Faldorn, Laelia) rather than just casting any spell.
- Impulse draw patterns use double-clause matching (`exile % you may % play/cast`) to avoid matching blink effects, which never include the "you may play" language.
- Cascade and discover are matched using `'Cascade' = ANY(keywords)` / `'Discover' = ANY(keywords)` (keyword array column populated from MTGJSON) rather than oracle text LIKE patterns, which avoids false positives from flavor text or card names.
- This event has natural overlap with spellslinger (`spell_cast`) and storm strategies: cascade and impulse draw both generate additional spell casts, making `play_from_exile` a natural companion archetype for those strategies. The `play_from_exile` producer pool and `spell_cast` producer pool intentionally overlap — cascade cards and impulse draw spells benefit both consumer types.

---

## Tribal Synergies

16 tribes × 3 pattern types = 48 trigger events, all generated programmatically from `TRIBES`.

**Tribes (in rough Commander popularity order):**
Dragon, Elf, Zombie, Vampire, Eldrazi, Human, Dinosaur, Goblin, Angel, Pirate, Wizard, Assassin, Merfolk, Cat, Sliver, Wolf

### Pattern types per tribe

| Suffix | Consumer regex | Producer SQL |
|---|---|---|
| `_cast` | `whenever you cast (a/an) {Tribe}` | `type_line LIKE '%{tribe}%'` |
| `_etb` | `whenever (a/another) {Tribe} ~ enters` | `type_line LIKE '%{tribe}%'` |
| `_lord` | `(other) {Tribe}s (you control/you own) ~ (get/have/gain)` | `type_line LIKE '%{tribe}%'` |

All three patterns for a tribe share the same producer pool — creature cards of that type. This means a lord effect on Dragon cards pairs with all Dragon creatures as producers.

### Type-line tribal edges (compute_tribal_typeline_synergy)

A separate pipeline stage (`compute_tribal_typeline_synergy`) generates commander→member and member→member synergy edges for each tribe.

**Commander qualification (oracle-text gating):** A Legendary creature qualifies as a tribal commander only if the tribe name appears in its `oracle_text`. Matching solely on `type_line` caused false positives — every Legendary Human would have been paired with all Humans, every Legendary Zombie with all Zombies, etc., even for commanders with no tribal-matters text (e.g. Kenrith, the Returned King; Kraum, Ludevic's Opus). The oracle-text requirement eliminates these spurious edges while preserving genuine tribal commanders like Wilhelt (Zombie in oracle text) and The Scarab God (not a Zombie by type, but references Zombies in oracle text).

**Member qualification (type-line only):** Tribe members are still identified purely by `type_line` — a card is a Zombie member if its type_line contains "Zombie". This side of the query is unchanged.

**Changeling support:** Cards with `'Changeling' = ANY(keywords)` are every creature type simultaneously, so they are included in every tribe's member pool. This ensures cards like Mothdust Changeling appear as Zombie members (and Dragon members, Elf members, etc.) even though their `type_line` only shows "Creature — Shapeshifter". The same changeling extension applies to the PRODUCER_MAP tribal entries used in `compute_synergy`.

Two edge classes per tribe, both stored as `score_type = 'ability_trigger'`:

| Class | card_a | card_b | Cap |
|---|---|---|---|
| `commander_member` | Legendary creature whose `oracle_text` mentions the tribe name | Any creature of that tribe (+ changelings) | Uncapped |
| `member_member` | Any creature of that tribe (+ changelings) | Any creature of that tribe (+ changelings) | `TRIBAL_MEMBER_LIMIT` (default 50 000) |

`TRIBAL_MEMBER_LIMIT` env var controls the member→member cap. Increase it (or set to 0 to disable) per tribe if needed. Commander→member edges are always inserted in full because those sets are small.

### Cross-synergy overrides

Two tribes have their producer pools extended to capture their natural deckbuilding companions:

#### Zombie × Reanimator
Zombie tribal producers (`tribal_zombie_cast/etb/lord`) include **reanimation spells** in addition to zombie-type cards:
- `return target % creature % graveyard % battlefield`
- `creature card from % graveyard % battlefield`
- `put target % creature % graveyard % battlefield`
- `unearth`

**Why:** Zombie decks are almost always also Reanimator decks. Wilhelt, Muldrotha, and The Scarab God all leverage the graveyard as a resource. Reanimation spells pair naturally with zombie payoffs even when the reanimated creature isn't a zombie.

#### Angel × Lifegain
Angel tribal producers (`tribal_angel_cast/etb/lord`) include **lifegain spells** in addition to angel-type cards:
- `you gain % life`, `gain life`, `gains life`, `lifelink`, `life equal to`

**Why:** Angel tribal decks (Giada, Lyra Dawnbringer, Speaker of the Heavens) almost universally run a lifegain sub-theme. Lifelink creatures and life-payment effects are natural complements.

---

## Activated Abilities

These tag cards that have on-board activated abilities, creating edges with cards that enable or protect those abilities.

| Event | Consumer regex | Producer SQL |
|---|---|---|
| `activated_tap` | `{T}:` — tap-activated ability | *(no producer — self-referential; only consumers are tagged)* |
| `activated_sacrifice` | `sacrifice ~ :` — sac-cost activated ability | *(no producer)* |

**Tuning notes:** These events have no PRODUCER_MAP entry. They tag cards that *have* activated abilities so the model learns which cards want to be on the battlefield (untapped, protected) vs. which cards want to sacrifice things. Future work: add producers (haste granters → tap ability enablers; token makers → sacrifice cost enablers).

---

## Design Principles

1. **Specificity hierarchy**: More specific events (`nontoken_etb`) are matched before broad catch-alls (`enters_battlefield`). A card can be tagged with multiple events if it matches multiple patterns.

2. **Producer precision over recall**: Producer SQL uses multi-word LIKE patterns ("creature card from % graveyard % battlefield") rather than single keywords ("battlefield") to reduce false positives.

3. **SYNERGY_LIMIT cap**: Each trigger event is capped at 500 000 edges. Very broad producers (legendary_matters with 30 000+ legendary cards) will be truncated. Increase `SYNERGY_LIMIT` env var if important edges are being missed.

4. **Cross-tribal coupling**: Zombie↔Reanimator and Angel↔Lifegain cross-synergies are encoded at the producer level, not the consumer level. This means the edges are directional: reanimation spells count as zombie producers, but zombie cards are not automatically reanimator producers.

5. **Theme overlap is intentional**: Equipment cards are producers for both `equipment_matters` and `artifact_matters`. An Equipment deck commander will see both equipment-specific and artifact-general payoffs as synergistic, which reflects actual deckbuilding.

---

## Commander-Value Synergies

Cards that gain a direct mechanical benefit from having a commander in play — either free casts, persistent in-play bonuses, or mana scaled by a legendary permanent's characteristics.  Edges use `score_type = 'commander_value'` (separate from `ability_trigger`) so Phase 2 training and the deck-scoring layer can query them independently.

### Trigger events

| Event | Consumer regex | Producer SQL | Score |
|---|---|---|---|
| `commander_free_cast` | `if you control a commander … without paying its mana cost / costs less / free` | Legendary creature or planeswalker with CMC ≤ 2 | 1.0 |
| `commander_in_play_payoff` | `as long as / while you control a commander` or `if you control a commander, [do something]` | Legendary creature or planeswalker with CMC ≤ 2 | 0.8 |
| `commander_mana_value` | `legendary (creature\|planeswalker) … (mana\|add)`, `add … legendary (creature\|planeswalker)`, or `mana value … legendary (creature\|planeswalker)` | Any legendary creature or planeswalker (no CMC cap) | 0.6 |

### Why CMC ≤ 2 for producers

`commander_free_cast` and `commander_in_play_payoff` cards scale in value with how often the commander is actually in play.  A CMC-0 or CMC-1 commander (Rograkh, Yoshimaru, Isamaru) is almost always available; CMC-2 (Thrasios, Kraum, Lurrus) recasts cheaply after removal.  CMC-3+ commanders spend meaningful resources on the command-zone tax, so the synergy is weaker and those commanders are excluded from the producer pool for these two events.

`commander_mana_value` (Mox Amber, Jeska's Will) works with any legend on the battlefield, so there is no CMC cap for that event.

### Canonical examples

| Card | Event tagged |
|---|---|
| Deflecting Swat, Fierce Guardianship, Flawless Maneuver, Deadly Rollick, Obscuring Haze | `commander_free_cast` |
| Loyal Apprentice, Jeska's Will (second mode) | `commander_in_play_payoff` |
| Mox Amber, cards referencing "legendary creature … mana" | `commander_mana_value` |

### Archetype hint integration

When a commander has CMC ≤ 2, the `analyze_commander_oracle_text()` endpoint adds `commander_value` to `boost_overrides` and includes a `"low-MV commander / commander-value staples"` note in `archetype_hint`.  This hint combines additively with tribal hints (e.g. a CMC-1 Elf tribal commander receives both hints).

---

## Known Gaps / Future Work

| Gap | Notes |
|---|---|
| `draw` removed | Draw synergies were too noisy (every deck runs draw). Re-add as `wheel` (group draw/discard events specifically) if needed. |
| `activated_tap` / `activated_sacrifice` | No producer map yet — these tag consumers but produce no edges. |
| Ninjutsu | "Return an unblocked attacker you control" — a distinct ninjutsu/evasion theme. |
| Type-line tribal | Addressed by `compute_tribal_typeline_synergy` stage — Zombie/Dragon/etc. creatures now have direct synergy edges from shared type_line, not just oracle-text trigger patterns. |
| Partner commanders | Cross-color identity synergies not modeled. |
| Storm / combo | "Win the game" or "deal infinite damage" patterns would require templating beyond regex. |
| Vehicle crew | `crew {N}` is not captured as a distinct synergy event. |
| Monarch / initiative / dungeons | Game-state-dependent mechanics not modeled. |
| Morbid precision | `morbid` is currently grouped with `graveyard_fill`; it's really closer to `nontoken_dies`. |
| Self-discard vs. group-discard | `discard` event mixes Nekusar (opponents discard) and Madness (self-discard) strategies. |
| Skullclamp indirect synergy | **Resolved** via `skullclamp_target` proxy edge — any 1-toughness token producers tagged as producers; Skullclamp tagged as consumer. See [Issue #2](https://github.com/cobaltroad/mtg-pytorch/issues/2). Eval step (confirm nearest neighbours) remains as future manual verification. |
