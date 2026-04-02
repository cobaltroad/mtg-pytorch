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
scores.  The primary type is **ability_trigger**: the `tag_mechanic_tags` stage
tags every card with its deck-key roles (e.g. `sac_outlet`, `counter_trigger`,
`tribal_elf`) using direct SQL against card columns — no oracle-text regex
pass required.  `compute_textmatch_synergy` then builds producer/consumer edges
from those role tags, which become the positive examples for Phase 2 training.

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

**Loss:** NT-Xent (InfoNCE) with cosine-annealed temperature.
**Data:** `synergy_edges` table — ability_trigger, role_demand, combo_package,
and commander_value edges.  Does card B provide something that triggers or
enables card A?
**Negatives:** 50% hard negatives (semantically similar but non-synergistic,
mined from the embedding space) + 50% random, at 3× the positive rate.
**Goal:** the encoder learns to pull synergistic cards together in latent space.

NT-Xent is used instead of BCE because BCE on cosine similarity has a degenerate
minimum: the model can achieve near-zero BCE loss while leaving Phase 1 geometry
largely intact, producing gradients that amplify surface-text features rather
than semantic synergy.  NT-Xent's in-batch negatives guarantee contrastive
signal in every batch; temperature annealing (high → low) provides an
easy-gradient warmup before sharpening clusters.

Because the synergy table can reach 100 M+ rows, training samples 500 k
positives with `TABLESAMPLE SYSTEM(10)` rather than a full table scan.

Final loss benchmark: >0.65 = barely learning, 0.55–0.60 = good,
0.45–0.50 = excellent.

### Phase 3 — Commander synergy ranking

**Loss:** Bayesian Personalised Ranking (BPR).
**Data:** `mtg_commanders.pt` — synthetic per-commander positive sets derived
from `synergy_edges` (ability_trigger + commander_value edges).
**Triples:** (commander, synergy-positive card, color-legal random negative).
**Goal:** the encoder learns that cards synergistic with a given commander
should score higher than arbitrary color-legal cards.

Training without human decklists is deliberate — see
[Training data: two-artifact design](#training-data-two-artifact-design) below.

#### What Phase 3 actually learns

The artifact already encodes which cards belong with which commander as binary
labels.  Phase 3 does not rediscover these relationships — it learns a
continuous scoring function over them.  The quantitative output is a ranking
model that can score any (commander, card) pair at inference without querying
the artifact, and that generalises to:

- **New cards** (new set releases) not yet decomposed into any positive set
- **Commanders with thin coverage** — those whose oracle text matched few
  patterns still benefit from embedding-space interpolation with similar commanders
- **Smooth interpolation** — commanders with similar archetypes share geometry
  even though their positive sets were built independently

As artifact coverage approaches 100%, Phase 3 loss increasingly measures
*memorisation fidelity* rather than generalisation.  A low BPR loss in that
regime means the model faithfully reproduces the artifact's judgements; the
true generalization test is recall on held-out commanders and `eval_deck.ps1`
output on commanders with sparse decompose coverage.

### Phase 4 — Generative deck construction

**Loss:** InfoNCE with 64 random negatives per position, temperature=0.1.
**Architecture:** the DeckConstructor decoder with the Phase 3 CardEncoder
weights loaded.  The encoder is **unfrozen** by default (controlled by
`-FreezeEncoder` in `run.ps1`); `--encoder-lr-scale` (default 0.01×) keeps
the encoder's learning rate well below the decoder's to protect Phase 3
representations.
**Data:** the same `mtg_commanders.pt` synthetic decks.  For each position the
model predicts the next card given the commander and the cards selected so far.
Sampled freely at inference — not greedy.

#### What Phase 4 actually learns

The artifact's per-commander positive sets are flat: all included cards are
equally weighted with no sense of order, priority, or diminishing returns.
Phase 4 adds the one signal the artifact cannot provide — **sequential
composition**.  Given a partial deck, the decoder learns:

- **Conditional selection** — after picking ramp and draw, interaction becomes
  more valuable; a second copy of a role already well-covered scores lower
- **Type diversity** — the attention context over the partial deck discourages
  piling on any single category
- **Role completion** — early picks shape what the decoder considers "missing"
  in subsequent positions

The practical consequence is that the same card may score differently at
position 10 versus position 60, depending on what the deck already contains.
This is the generative capability the heuristic scoring pipeline in
`services/api/ops/deck/generate.py` approximates with its iterative
re-scoring loop.

Keeping the encoder's learning rate low (1% of decoder lr) is important:
aggressive encoder updates can destroy Phase 3 synergy geometry, causing
score compression (all cosine similarities collapse toward 1.0).  Pass
`-FreezeEncoder true` in `run.ps1` to freeze the encoder entirely if this
becomes a problem.

---

## Training data: two-artifact design

Training uses two separate `.pt` artifacts rather than one:

| Artifact | Phases | What it encodes |
|----------|--------|-----------------|
| `mtg_dataset.pt` | 1–2 | Card embeddings + pairwise synergy edges |
| `mtg_commanders.pt` | 3–4 | Per-commander synthetic decks from `synergy_edges` |

### Why not train Phases 3–4 on human decklists?

The failure mode is **representation collapse**.  Every Commander deck needs
the same baseline roles — draw, ramp, removal — regardless of what the
commander actually does.  When the Phase 3 BPR objective sees (Atraxa, Praetors'
Voice → Arcane Signet) and (Prossh, Skyraider of Kher → Arcane Signet) as
both positive triples, the gradient signal pushes every commander toward the
same generic-staple region of embedding space.  After training, all commanders
end up in an indistinct high-similarity cluster: the model cannot distinguish
"I'm building around Prossh's sacrifice trigger" from "I'm building around
Atraxa's proliferate".

This only gets worse the further a commander is from the decklists in training
data.  For popular commanders the co-occurrence signal is strong; for the
thousands of commanders with zero or few imported decks, Phase 3 provides no
useful gradient at all — the encoder representation is frozen in Phase 2 geometry.

### Why the commander artifact solves this

`export_dataset_commanders.py` reads `synergy_edges` directly and builds
per-commander positive sets from two edge types:

- **ability_trigger** — cards that produce events the commander cares about
  (e.g. for Syr Konrad, the Grim: every card that causes creatures to die or
  enter graveyards is a producer).
- **commander_value** — cards the commander's text explicitly rewards
  (e.g. for Prossh: token producers and sacrifice outlets).

Color-identity legality is re-applied strictly (⊆) so the negative pool is
always confined to what is actually playable.  Because these positive sets
are derived from the commander's own oracle text and ability structure, they
are genuinely distinct per commander — Prossh's positives overlap very little
with Atraxa's, giving BPR a meaningful gradient for each.

Commanders with fewer than 10 producer cards are skipped (`COMMANDERS_MIN_POS`),
so every training example has a real signal.  The artifact covers ~3,000 legal
commanders versus the ~171 decklists previously imported from Moxfield/cardtrak,
dramatically improving coverage of the commander space.

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
| Moxfield exports | Human decklists (imported for proxy context at inference; not used for Phase 3/4 training) |
| cardtrak | Internal collection tracker; additional decklist source |
| XMage source | Java reference implementation; used to cross-check ability pattern extraction |

---

## Operational notes

See `CLAUDE.md` for the full development workflow, two-environment setup
(Docker host for serving, GPU machine for training), training scripts, and
infrastructure lessons learned.
