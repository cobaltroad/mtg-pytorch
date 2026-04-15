# XMage as a training signal

XMage's Java card implementations encode machine-readable ability structure
(triggered, activated, static, keyword) via typed Java classes.
`tag_abilities_xmage` supplements the canonical `tag_mechanics` stage with
a pass over the XMage Java source tree (`mage/`) and writes `card_abilities`
rows tagged `source='xmage'`.

## How `xmage_parse.py` works

`parse_java_file(path)` returns `(ability_classes, effect_classes, trigger_event_overrides)`:

- **ability_classes** — XMage ability class names extracted from `import mage.abilities.common.*` and `import mage.abilities.keyword.*` statements.
- **effect_classes** — effect class names from `import mage.abilities.effects.common.*`.
- **trigger_event_overrides** — `dict[str, str]` mapping ability class → refined `trigger_event` for any class where a body scan can narrow the default.  Empty dict when nothing is refined.

`ABILITY_CLASS_TO_EVENT` maps class names to generic `trigger_event` strings (e.g. `SpellCastControllerTriggeredAbility` → `"spell_cast"`).  The caller resolves `trigger_event_overrides.get(ac) or ABILITY_CLASS_TO_EVENT.get(ac)` so body-scan results take priority.

Adding a new body-scan refinement is two steps: add a regex + lookup in the body-scan block of `parse_java_file`, and populate the result into `trigger_event_overrides`.  No signature change needed.

The upsert uses `ON CONFLICT DO UPDATE SET trigger_event = EXCLUDED.trigger_event` so re-running `tag_abilities_xmage` refreshes refined values without a manual delete.

## SpellCastControllerTriggeredAbility filter refinement

XMage uses one class (`SpellCastControllerTriggeredAbility`) for all "whenever you cast X" triggers but passes a `StaticFilters.FILTER_SPELL_*` constant as the second constructor argument to restrict the spell type.  Without refinement all 96 cards using this ability land in a single `spell_cast` bucket — putting Sythis (enchantment cast) in the same positive-pair cluster as Guttersnipe (instant/sorcery) and Beast Whisperer (creature cast), which corrupts Phase 2 NT-Xent training.

**How it works end-to-end:**

1. `parse_java_file` scans the Java body for `StaticFilters.(FILTER_SPELL_\w+)` when `SpellCastControllerTriggeredAbility` is imported.  `SPELLCAST_FILTER_MAP` translates the constant to a refined `trigger_event` (e.g. `FILTER_SPELL_AN_ENCHANTMENT` → `"enchantment_cast"`), which is stored in `trigger_event_overrides`.

2. `tag_abilities_xmage` writes the refined `trigger_event` into `card_abilities`.

3. `compute_xmage_synergy` (in `pipeline.py`) detects `SpellCastControllerTriggeredAbility` and queries the distinct `trigger_event` values present in `card_abilities` for that class.  Each sub-bucket is processed independently using `SPELLCAST_TRIGGER_PRODUCER_MAP` in `synergy/xmage.py` to select the correct producer cards (e.g. only enchantments for `enchantment_cast`).

| `trigger_event` | Producer cards selected |
|---|---|
| `enchantment_cast` | cards with `enchantment` in type_line |
| `artifact_cast` | cards with `artifact` in type_line |
| `creature_cast` | cards with `creature` in type_line |
| `instant_sorcery_cast` | instants and sorceries |
| `noncreature_cast` | non-creature, non-land cards |
| `historic_cast` | artifacts, legendaries, sagas |
| `spirit_arcane_cast` | spirits and arcane spells |
| `spell_cast` | any non-land card (generic fallback) |

Cards with no recognised `StaticFilters` argument keep `trigger_event='spell_cast'` and use the generic `_ANY_SPELL` producer SQL.
