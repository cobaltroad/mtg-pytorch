# MTG Commander AI

A PyTorch system that learns to build 99-card Commander decks from a single
commander card.  The goal is *model-discovered* decklists — human decks are
training signal, not output target.

---

## Approach overview

Deck building is decomposed into three learnable stages, each building on the
previous:

1. **Card representation** — embed every card into a dense vector space where
   semantically similar cards are geometrically close.
2. **Synergy and co-occurrence learning** — fine-tune those representations so
   cards that belong together in a deck are close to each other and to the
   commander they serve.
3. **Generative deck construction** — a transformer decoder that, given a
   commander and a partial deck, predicts which card to add next.

---

## 1. Card embeddings

### Base embeddings

Each card's oracle text is passed through
`sentence-transformers/all-mpnet-base-v2` to produce a 768-dimensional vector.
This gives a strong semantic baseline: cards with similar rules text end up
near each other in embedding space without any MTG-specific training.

### Land augmentation

Raw oracle text is a poor signal for land quality.  A dual land reads
`{T}: Add {B} or {G}.` — short and generic — while a utility land like Boseiju
has rich, distinctive text.  Embedding from oracle text alone would score
Boseiju higher than Overgrown Tomb, which is backwards.

Before embedding, the ingest pipeline prepends structured tags to every Land
card's oracle text (`services/ingest/land_tags.py`).  Tags encode:

- **Primary type** — `[FETCH_LAND:BG]`, `[DUAL_LAND:BG]`, `[ANY_COLOR_LAND]`, etc.
- **Cycle** — `[SHOCK_LAND]`, `[CHECK_LAND]`, `[FAST_LAND]`, `[PAIN_LAND]`, etc.
- **Penalty** — `[ENTERS_TAPPED]`, `[DOESNT_UNTAP]`, `[CONDITIONAL_SACRIFICE]`, etc.

At the raw embedding level (before any training), fetch lands cluster with
other fetch lands, shock lands cluster with check lands and BG duals, and
Woodland Cemetery's nearest neighbour is Overgrown Tomb.

### Synergy edges

The ingest pipeline also builds a `synergy_edges` table of pairwise synergy
scores.  The primary type is **ability_trigger**: for each triggered-ability
pattern on card A (e.g. "whenever a creature dies") the pipeline identifies
every card B whose abilities could fire that trigger (e.g. a sac outlet).
These producer/consumer pairs become the positive examples for Phase 2
training.

Tribal synergy edges are computed separately, including changeling-aware
matching so cards like Maskwood Nexus correctly count as members of every tribe.

---

## 2. Training

Training runs in four phases on a GPU machine.  Each phase warm-starts from
the previous phase's checkpoint.  The model architecture is:

- **CardEncoder** — a two-layer MLP (768 → 512 → 256) with GELU, LayerNorm,
  Dropout 0.1, and L2 normalisation on the output.  Projects pre-computed
  embeddings into a 256-dim shared latent space.

- **DeckConstructor** — a 3-layer transformer decoder (4 heads, 256-dim) that
  cross-attends to the commander embedding as a single memory token and
  self-attends over the partial deck sequence.  Scores candidates by dot
  product against the mean deck context vector.

### Phase 1 — Text equivalence

**Loss:** contrastive (InfoNCE-style).
**Positives:** different printings of the same oracle ID (reprints with
identical rules text).
**Goal:** the encoder learns that identical-text cards should be nearest
neighbours regardless of art, set, or collector number.

This is a sanity-check phase — it verifies the encoder can distinguish cards
at all and gives it a meaningful starting geometry.

### Phase 2 — Ability-trigger synergy

**Loss:** binary cross-entropy.
**Data:** `synergy_edges` table (ability_trigger type) — does card B provide
something that triggers or enables card A?
**Negatives:** randomly sampled non-synergistic pairs at 3× the positive rate.
**Goal:** the encoder learns to pull synergistic cards together.

Because the synergy table can reach 100 M+ rows, training samples 500 k
positives with `TABLESAMPLE SYSTEM(10)` rather than a full table scan.

Final loss benchmark: >0.65 = barely learning, 0.55–0.60 = good,
0.45–0.50 = excellent.

### Phase 3 — Deck co-occurrence ranking

**Loss:** Bayesian Personalised Ranking (BPR).
**Data:** human-built Commander decklists imported from Moxfield and cardtrak,
stored as (commander, card list) pairs.
**Triples:** (commander, positive card from a real deck, random negative card).
**Goal:** the encoder learns that cards co-occurring with a given commander
should score higher than random cards.

More data consistently lowers loss: 94 decks → 0.54, 120 → 0.43, 278 → 0.39,
344 → 0.28.  Importing more decklists is the primary lever for improving
Phase 3 quality.

### Phase 4 — Generative deck construction

**Loss:** InfoNCE with 64 random negatives per position, temperature=0.1.
**Architecture:** the DeckConstructor decoder with the Phase 3 CardEncoder
weights loaded.  The encoder is **unfrozen** by default (controlled by
`-FreezeEncoder` in `run.ps1`); `--encoder-lr-scale` (default 0.1×) keeps
the encoder's learning rate well below the decoder's to protect Phase 3
representations.
**Data:** the same human decklists, now treated as ordered sequences; for each
position the model predicts the next card given the commander and the cards
selected so far.  Sampled freely at inference — not greedy.

Keeping the encoder's learning rate low relative to the decoder is important:
with only a few hundred decks, aggressive encoder updates can memorise training
sequences and destroy Phase 3 synergy geometry, causing score compression (all
cosine similarities collapse toward 1.0).  Pass `-FreezeEncoder true` in
`run.ps1` to freeze the encoder entirely if this becomes a problem.

---

## 3. Deck building at inference

Deck generation is a pipeline of model scoring followed by deterministic
structural enforcement.  The entry point is `services/api/ops/deck/generate.py`.

### Scoring

For each candidate card in the card pool (filtered to the commander's color
identity), the pipeline computes a composite score:

```
final_score = (1 - α) × model_score + α × synergy_score
```

- **model_score** — cosine similarity between the candidate's encoder output
  and the commander's encoder output, boosted by role-specific multipliers
  (tribal match, evasion, removal, value engine, commander-value text patterns).
- **synergy_score** — mean pairwise synergy edge score between the candidate
  and the cards already selected in the current iteration.
- **α (synergy_alpha)** — blend weight, default 0.4 (40% synergy, 60% model).
  Adjustable per request.

### Role-based multipliers (heuristic layer)

Several scoring modules apply score multipliers based on rule detection:

| Module | What it boosts |
|--------|---------------|
| `ramp.py` | Mana producers (rocks, dorks, land-ramp) by land quality tier; Sol Ring / Arcane Signet are guaranteed includes |
| `evasion.py` | Flying, trample, menace, unblockable enablers when the commander wants to attack |
| `removal.py` | Hard removal (exile/destroy) and board wipes |
| `value_engine.py` | Card draw and card advantage engines |
| Tribal boost | 1.5× for creatures that share a creature type with the commander's tribal identity |
| Commander-value | 1.4× for cards whose text references controlling a commander |

### Structural enforcement

After scoring, the deck is assembled deterministically to hit structural targets:

| Slot | Target |
|------|--------|
| Ramp | 10 spells (Sol Ring + Arcane Signet guaranteed) |
| Lands | 36 total; up to 20 nonbasics (Command Tower + Exotic Orchard guaranteed); basics fill to 36 |
| Non-land spells | 63, distributed across a mana curve: 8 × 1-drop, 16 × 2, 14 × 3, 12 × 4, 7 × 5, 6 × 6+ |

Ramp is selected first (highest-scoring mana producers), then the remaining
spell slots are filled iteratively — each step re-scores candidates against
the partial deck to maximise synergy density.  Lands are scored separately
by mana quality (land embedding quality tier × color match).

### Iterative selection and synergy density

Rather than selecting all cards in one pass, the spell selection loop adds
one card per step and recomputes synergy scores after each addition.  This
means early high-synergy picks make later synergistic cards score higher —
the deck self-reinforces its own theme as it builds.

The final deck JSON reports `synergy_density` (mean pairwise synergy score
across all card pairs) and `synergy_baseline` (what a random same-color deck
would score), so the UI can show how much denser the generated deck is than a
random baseline.

### Commander analysis

Before generation (or as a standalone call), `GET /commanders/{oracle_id}/analyze`
runs a pure-heuristic parser over the commander's oracle text to extract
structured signals: tribal identity, combat themes, counter/token strategies,
MTG rules-term mechanics (e.g. "mana ability" → elfball engine), and anything
the parser couldn't interpret (gaps).  The analysis produces `boost_overrides`
— a list of scoring multiplier keys — that are passed directly to generation.

---

## Data sources

| Source | Role |
|--------|------|
| MTGJSON AtomicCards | Primary card data — downloaded and cached by the ingest pipeline |
| Commander Spellbook | Combo packages; used to boost completion of near-complete combo lines |
| Moxfield exports | Human decklists for Phase 3/4 training |
| cardtrak | Internal collection tracker; additional decklist source |
| XMage source | Java reference implementation; used to cross-check ability pattern extraction |

---

## Operational notes

See `CLAUDE.md` for the full development workflow, two-environment setup
(Docker host for serving, GPU machine for training), training scripts, and
infrastructure lessons learned.
