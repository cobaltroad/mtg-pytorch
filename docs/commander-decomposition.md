# Commander decomposition (`stages/decompose.py`)

Decomposes all ~3,000 legal commanders into structured synergy signals.  **Required pipeline step** — writes `card_abilities` rows with `source='decompose'` that `export_dataset_commanders` reads when building per-commander positive sets.  Also drives the UI decompose panel (`GET /commanders/{oracle_id}/decompose`).

Run after `process` (requires populated `cards` and `card_abilities` tables), and before `compute_commander_value_synergy`.

```bash
docker compose run --rm ingest python pipeline.py --stage decompose_commanders
```

Detection logic lives in `stages/decompose.py` (`ORACLE_PATTERNS`, `_detect()`).  `export_dataset_commanders.py` reads the resulting `card_abilities` rows — it does **not** call `_detect()` directly.  This ensures the UI and the training artifact are always consistent.

## Signal sources

Each `card_abilities` row written by `decompose_commanders` comes from:

| Source | How it works |
|--------|-------------|
| `oracle_text` | ~32 regex patterns in `ORACLE_PATTERNS` against the commander's rules text.  Each match produces a `pattern_key` (`trigger_event`), human-readable label (`ability_name`), and matched phrase (`raw_text`). |

## Pattern library (oracle text)

| Pattern key | Description | Notable commanders |
|---|---|---|
| `etb_trigger` | ETB trigger (generic + proper-name subjects) | Panharmonicon payoffs |
| `attack_trigger` | Attack trigger | Isshin, Raiyuu, Gahiji |
| `cast_trigger_creature` | Creature cast trigger | Beast Whisperer analogues |
| `cast_trigger_instant_sorcery` | Instant/sorcery cast trigger | Guttersnipe analogues |
| `cast_trigger_enchantment` | Enchantment cast trigger | Sythis, Eidolon |
| `cast_trigger_artifact` | Artifact cast trigger | Breya, Daretti |
| `cast_trigger_historic` | Historic spell cast trigger | Jhoira, Teshar, Sarah Jane Smith |
| `cast_trigger_colored` | Color-based cast trigger | Chandra, K'rrik, Aragorn |
| `death_trigger` | Creature death trigger | Syr Konrad, Teysa |
| `graveyard_from_play` | Permanent to graveyard | Meren |
| `graveyard_payoff` | Cast/return from graveyard | Karador, Muldrotha |
| `unearth_encore` | Unearth / encore / temporary reanimation (haste, exile/sacrifice at end step) | Sedris, Burakos, Feldon |
| `combat_damage_to_player` | Combat damage to player | Voltron payoffs |
| `sacrifice_payoff` | Sacrifice outlet/payoff | Prossh, Korvold |
| `discard_outlet` | Discard outlet | Anje, Waste Not |
| `madness_payoff` | Madness | Anje Falkenrath |
| `landfall` | Landfall | Omnath variants |
| `counter_placement` | +1/+1 counter placement | Atraxa, Ezuri |
| `counter_doubler` | Counter doubling | Vorinclex |
| `proliferate_matters` | Proliferate | Atraxa |
| `lifegain_trigger` | Life gain trigger | Oloro, Dina |
| `draw_trigger` | Draw trigger | Niv-Mizzet |
| `token_trigger` | Token creation trigger | Brudiclad |
| `trigger_doubling` | Trigger doubling | Isshin, Wulfgar |
| `keyword_lord` | Keyword grant to creatures | Odric, Akroma |
| `cycling_trigger` | Cycling trigger | Gavi, Ominous Seas |
| `second_spell` | Second spell matters | Veyran |
| `punisher` | Damage/drain each opponent | Mogis, Nekusar |
| `weenie_matters` | Low-power creature payoff | Edric |
| `extra_combat` | Extra combat phase | Aurelia, Moraug, Raiyuu |
| `equipment_matters` | Equipment ETB/attack/static | Kemba, Sram, Wyleth, Akiri |
| `artifact_count` | Artifact count matters (scales with # of artifacts) | Akiri Line-Slinger, Saheeli, Muzzio, Alibou |
| `artifact_creatures` | Artifact creatures matter (buffs/triggers off artifact creatures) | Alibou, Brudiclad, Padeem, Teshar, Sydri |
| `opponent_restriction` | Opponents can't (stax) | Narset, Dragonlord Dromoka |
| `activated_restriction` | Activated abilities locked (stax) | Linvala, Karn |
| `tax_effect` | Opponents' spells cost more | Grand Arbiter |
| `enters_tapped_opponent` | Opponents' permanents enter tapped | Thalia Heretic Cathar |
| `monarch` | Monarch mechanic | Queen Marchesa, Aragorn |
| `initiative` | Initiative mechanic | Rilsa Rael, Safana |
| `goad` | Goad | Karazikar, Marisi, Kitt Kanto |
| `forced_attack` | Attacks each combat if able | Thantis, Zurgo, Toski |
| `poison_infect` | Infect / toxic / poison counter | Skithiryx, Fynn, Ixhel |
| `cascade` | Cascade / discover | Yidris, Abaddon, Maelstrom Wanderer, Averna |
| `group_hug` | Draw/resource grants to all players | Kami, Kwain, Kynaios and Tiro |

## Spot-check with `eval_decomposition`

```bash
# Named lookup (partial, case-insensitive):
docker compose run --rm ingest python -m scripts.eval_decomposition "Anje"
docker compose run --rm ingest python -m scripts.eval_decomposition "Syr Konrad, the Grim"

# Gap analysis — commanders with zero signals:
docker compose run --rm ingest python -m scripts.eval_decomposition --no-signals

# Evaluate a specific pattern key:
docker compose run --rm ingest python -m scripts.eval_decomposition "Anje" --key discard_outlet
docker compose run --rm ingest python -m scripts.eval_decomposition "Anje" --key discard_outlet --limit 0  # remove per-key cap (default 10)
```

## Adding a new pattern

Add one entry to `ORACLE_PATTERNS` in `stages/decompose.py`:

```python
("pattern_key",
 "Human-readable label",
 re.compile(r"your regex here", re.I)),
```

Then re-run `decompose_commanders` and spot-check with `eval_decomposition --key <key>`.  No other files require modification.
