# MTG-PyTorch TODO

## Decklist Acquisition

### The Core Tension

The model's goal is to **discover synergies from the embedding space**, not mirror existing decklists. Decklists are training signal, not ground truth — the model should generalise beyond them. That said, more decklists per theme give the model better signal to learn which cards actually cluster together.

### Quantity Guidelines (rough heuristics)

| Decks per theme | Expected outcome |
|---|---|
| < 50 | Noise floor — model likely learns nothing reliable for that theme |
| 50–150 | Weak signal; model may capture the strongest, most obvious synergies only |
| 150–500 | Moderate signal; main archetypes learnable, fringe cards noisy |
| 500–1,500 | Strong signal for most themes |
| 1,500–5,000 | Near-ceiling for individual theme learning |

**Current dataset: ~94 decks total.** This is below the noise floor for most individual themes.

### Priority Themes (most underrepresented)

Add decklists for these commanders / strategies first:

1. **Aristocrats / Sacrifice** — e.g. Teysa Karlov, Mahadi, Meren of Clan Nel Toth
   - Critical gap: Skullclamp's real synergy is "sacrifice cheap creatures" (see below)
2. **Token Generation** — e.g. Rhys the Redeemed, Adeline, Jinnie Fay
   - Pairs with Aristocrats; tokens exist to be sacrificed or to attack en masse
3. **+1/+1 Counters** — e.g. Hamza, Vorel of the Hull Clade, Atraxa
4. **Equipment / Voltron** — e.g. Akiri, Wyleth, Syr Gwyn
5. **Spellslinger / Magecraft** — e.g. Mizzix, Veyran, Niv-Mizzet Parun
6. **Landfall** — e.g. Aesi, Omnath (any version), Mina and Denn
7. **Elfball / Creature Ramp** — e.g. Lathril, Selvala, Marwyn
8. **Goblin Tribal** — e.g. Krenko, Muxus — tests whether type_line-only tribal signal is learnable
9. **Aura / Enchantress** — e.g. Sythis, Tuvasa, Zur
10. **Reanimator** — e.g. Karador, Muldrotha, Gisa and Geralf

---

## Known Model Limitations

### Skullclamp: Emergent / Indirect Synergy

Skullclamp's optimal play pattern is:

1. Play cheap, expendable creatures (tokens, 1/1s)
2. Equip Skullclamp → creature becomes a 1/0 and dies immediately
3. Draw 2 cards for 1 mana

This means Skullclamp's *real* synergy partners are:
- **Token generators** (Raise the Alarm, Krenko, etc.)
- **Aristocrats payoffs** (Zulaport Cutthroat, Blood Artist, etc.)
- **Sacrifice outlets** (Goblin Bombardment, etc.)

**The problem**: No regex pattern can capture "this card wants cheap creatures to die". The synergy is emergent — it requires the model to see Skullclamp co-piloted with token generators and sacrifice effects across many decklists.

**Fix**: Add 50+ Aristocrats/Token decks that include Skullclamp. The model will then learn that Skullclamp clusters with those archetypes rather than vanilla equipment payoffs.

### Wilhelt: Type Line Tribal Signal

Wilhelt, the Rotcleaver cares almost exclusively about the **Zombie creature type** — a property encoded in `type_line`, not `oracle_text`. The model currently learns synergy primarily from oracle_text embeddings. A card that says nothing about Zombies but *is* a Zombie is still an ideal Wilhelt card.

**Current gap**: `card_embeddings` (from the NLP encoder on oracle_text) don't capture tribal membership. A card whose entire oracle text is "Tap: Add {B}" but whose type line is "Legendary Creature — Zombie" looks synergistic with nothing.

**Possible fixes** (pick one or combine):
- [ ] Add type_line as a concatenated field when computing NLP embeddings (changes embedding semantics globally)
- [ ] Add explicit synergy edges: for each tribal commander, all cards sharing the relevant creature type → synergy edge (no ML needed, pure rule)
- [ ] Encode type_line separately and concatenate with oracle_text embedding before feeding into CardEncoder
- [ ] Add many more Zombie tribal decklists and rely on co-occurrence to teach the model

---

## Color Identity Constraint

**This is a hard rule in Commander, not a learned preference.** Every card in a deck must be within the commander's color identity (all mana symbols in cost + rules text). Infernal Plunge ({R}) is mechanically perfect for Aristocrats strategies but is illegal in the most common Aristocrats commanders (Teysa Karlov {W}{B}, Meren of Clan Nel Toth {B}{G}, Karador {W}{B}{G}).

### Why this matters for the model

1. **synergy_edges are color-blind** — a red sacrifice outlet is marked synergistic with cards that will appear in WB decks. The Phase 2 training signal is polluted with cross-color false positives.

2. **Phase 3/4 negative sampling is color-blind** — random negatives are drawn from all 33k cards. Many "hard" negatives are simply off-color and trivially wrong, making the learning task easier than it should be. The model learns partial color separation as a proxy for synergy, rather than learning synergy directly.

3. **Inference produces illegal decks** — without a color identity filter, the DeckConstructor can recommend red cards for a WB commander.

### What color identity means in practice

The typical Aristocrats palette (W/B/G subsets) never touches red, so:
- Goblin Bombardment, Infernal Plunge, Viscera Seer (R) → always excluded from Teysa lists
- Phyrexian Altar (colorless) → legal in any deck
- Blood Artist (B) → legal in any deck with black

### Required fixes (in priority order)

- [ ] **Store color_identity per card** in the `cards` table. MTGJSON provides this as an array (`colorIdentity: ["W","B"]`). Currently only `colors` (casting cost) is stored; identity also includes mana symbols in oracle text.
- [ ] **Filter synergy edges at compute time**: when inserting edges, skip card pairs that share no plausible color identity overlap. This is approximate (edges are card-to-card, not commander-specific) but removes the worst cross-color noise.
- [ ] **Filter Phase 3/4 negative pool per commander**: in `train_deck_phase()` and `train_deck_constructor_phase()`, restrict the negative candidate pool to cards whose color identity is a subset of the commander's color identity. This makes the learning task honest.
- [ ] **Hard filter at inference**: before DeckConstructor scores candidates, exclude any card whose color identity is not a subset of the commander's. This is non-negotiable for producing legal decks.

---

## Phase 4 / DeckConstructor

- [ ] Evaluate phase4_best with `eval_synergy.py` — compare against phase3_best for Skullclamp, Wilhelt, Giada
- [ ] Consider a Phase 5: fine-tune on commander-stratified batches (one batch per commander archetype) to prevent majority-class commanders (e.g. generic goodstuff) from drowning out minority themes
- [x] Explore temperature annealing in InfoNCE (start 0.5 → end 0.05) rather than fixed 0.1 — implemented as cosine schedule via `--temp-start` / `--temp-end` in Phase 2 and Phase 4

---

## Data Pipeline

- [ ] Partner commanders with single-slash separator (e.g. `Tymna / Thrasios`) are currently skipped — `import_decklists.py` only splits on `//`. Either normalise on export or handle both separators
- [ ] `source_url` deduplication: ON CONFLICT DO NOTHING means re-importing the same decklist export is safe, but updating existing decks is not supported. Add `ON CONFLICT (source_url) DO UPDATE` if re-imports need to refresh card lists
- [ ] Consider exporting `deck_format` as a training signal — cedh decks have a very different card pool than casual EDH

---

## Infrastructure

- [ ] `eval_synergy.py` currently loads all 33k+ card embeddings into RAM for every query — add an optional `--filter-type` flag to restrict to a colour identity or card type for faster interactive use
- [ ] Add a `make eval` target to docker-compose for one-liner sanity checks
