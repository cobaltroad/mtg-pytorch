# MTG Commander AI

A PyTorch system that learns to build 99-card Commander decks from a single
commander card.  The goal is *model-discovered* decklists — human decks are
training signal, not output target.

---

## Approach overview

**The architecture is composition-first** (since PR #132; full design in
[`docs/composition-first-plan.md`](docs/composition-first-plan.md)).  A human
builds a deck top-down — legality constraints, then functional quotas, then
best-in-slot card selection — and so does this system.  Deterministic
composition is the skeleton; learned models only rank cards *within* slots:

1. **Legality & card facts** — color identity, singleton, structured pip
   parsing, land classification (`card_facts` table; never learned).
2. **Composition profile** — every quota (lands, ramp + max-MV ceiling, draw,
   removal, sweepers, protection, theme) *derived* from the commander's mana
   value, pips, and decompose signals, each carrying a "because" rationale
   (`shared/composition/profile.py`).
3. **Slot filling** — staple SQL pools + the commander's theme pool, ranked
   by the Phase 1/2 learned models; the model picks *which* 10 ramp pieces,
   never *how many*.
4. **Mana base & castability** — Karsten-style per-color source minimums and
   a Monte Carlo goldfisher gate every build
   (`shared/composition/{karsten,goldfish,builder}.py`).

The learned components (below) exist to serve step 3: embed every card into
a space where the bilinear relation head can score commander–card fit.
Current work is tracked in the
[composition backlog epic](https://github.com/cobaltroad/mtg-pytorch/issues/156)
(`docs/composition-next-steps.md`).

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
scores.  The primary type is **ability_trigger**: the `tag_mechanics` stage
tags every card with its deck-key roles (e.g. `sac_outlet`, `counter_trigger`,
`tribal_elf`) using direct SQL and oracle-text patterns.  Three phases:
coarse deck-key rows (`source='oracle_text'` or `'card_characteristic'`),
fine-grained sub-pattern rows, and ORACLE_PATTERNS applied to all cards.
`compute_textmatch_synergy` then builds producer/consumer edges from those
role tags, which become the positive examples for Phase 2 training.

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

### Phase 3 — Commander synergy ranking (retired)

> **Status: retired by the composition-first architecture** (#151).  Within-slot
> ranking uses the Phase 2 bilinear head; per-commander quota logic is
> deterministic.  Documentation kept for checkpoint archaeology.

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
true generalization test was recall on held-out commanders and the (since-removed) `eval_deck.ps1`
output on commanders with sparse decompose coverage.

### Phase 4 — Generative deck construction (retired)

> **Status: retired by the composition-first architecture** (#151).  Deck-level
> coherence (quotas, curve, diminishing returns, mana base) is now computed
> analytically rather than learned from ~94 decklists — see
> `docs/composition-first-plan.md` for the rationale.

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
The composition builder now provides this conditionality analytically —
curve-target capacity and per-sub-theme diminishing-returns counters
(`shared/composition/builder.py`) — which is why this phase was retired.

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

## 3. Deck building at inference — the composition engine

The build path is `POST /commanders/{oracle_id}/build?ranking=model|heuristic`
(`services/api/ops/composition.py`), backed by the pure engine in
`shared/composition/`.  One build runs:

1. **Profile derivation** — decompose signals are read from `card_abilities`
   (`source='decompose'`) and combined with the commander's MV and pips to
   derive every quota, each with a "because" string that flows to the UI
   ("10 ramp at ≤2 MV because the commander costs 4 and goes live turn 3").
   Protection scales with commander-centricity: voltron 6 > activated
   engine / multi-signal 5 > single-signal 3 > vanilla 2.

2. **Pool assembly** — staple SQL pools (`shared/mtg_sql/staples/`: ramp,
   draw engines/spells, removal + interaction, sweepers, protection) plus a
   per-commander **theme pool** from materialized `decomposed_candidates`
   synergy edges, whose `pattern_keys` metadata drives diminishing-returns
   counters (the 8th sac outlet loses its slot to the 1st token payoff).

3. **In-slot ranking** — with `ranking=model`, the Phase 1 encoder + Phase 2
   bilinear head (`decomposed_candidates` relation) reorder every pool
   except **ramp**, which stays heuristic: mana development is castability
   physics (mana output per card) the synergy model can't see.

4. **Mana base + castability gate** — nonbasics by quality score, basics
   allocated to Karsten per-color source minimums before pip-census
   proportionality, then a Monte Carlo goldfisher simulates hands and land
   drops.  A feedback loop converts theme slots into lands until
   P(commander cast on time) clears an MV- and pip-scaled floor.  The gate
   catches broken mana bases; it never promises a turn-N commander.

Decks persist to the Generated Decks history with the full composition block
(profile, breakdown, goldfish metrics, warnings) rendered by the UI.

### Regression harness

`docker compose run --rm ingest python -m scripts.eval_harness` builds a
golden 20-commander set and enforces hard invariants — 99 cards, singleton,
color identity (verified against source data), quota audit (only
builder-warned deviations tolerated), castability gate — plus a soft
comparison of quota censuses against imported human deck distributions.
Exit code 0/1; run before merging composition changes.

**When decompose patterns change** (`stages/decompose.py` ORACLE_PATTERNS or
`synergy/commander_mechanics.py` consumer SQL), re-run
`--stage decompose_commanders` and `--stage compute_commander_value_synergy`
so the API's materialized signals/edges match the code (staleness check
tracked in #137).

---

## Known gaps

The full backlog lives in the
[composition-first epic (#156)](https://github.com/cobaltroad/mtg-pytorch/issues/156);
`docs/composition-next-steps.md` carries scope and verification methods for
every item.  Highlights of what is still open:

- **~737 commanders fire zero decompose signals**
  (`python -m scripts.eval_decomposition --no-signals`) and some fired keys
  still lack consumer SQL — the systematic burn-down is #136.  Resolved
  exemplars: graveyard family / Muldrotha + Karador (#133), high-MV +
  a-player-casts / Kozilek + Niv-Mizzet (#134), activated tutor engines /
  Yisan (#135) — all verified by `commander_edge_rate` 0 → 1.00.
- **Materialized decompose data can drift from pattern code** — after any
  ORACLE_PATTERNS / consumer-SQL change, `decompose_commanders` and
  `compute_commander_value_synergy` must re-run; a staleness check is #137.
- **~950 newest cards have no embeddings** until `--stage embed_cards`
  re-runs (#139); model-ranked builds degrade gracefully (unscored cards
  tail-rank in heuristic order).
- **Goldfish fidelity**: commander cost reduction is a documented gate
  relaxation rather than simulated (#142); MDFC lands are classified but
  unused by the mana base (#143 territory alongside fetch-aware source
  counting #144).

---

## Data sources

| Source | Role |
|--------|------|
| MTGJSON AtomicCards | Primary card data — downloaded and cached by the ingest pipeline |
| Commander Spellbook | Combo packages; used to boost completion of near-complete combo lines |
| Moxfield exports | Human decklists (`import_moxfield.py`) |
| Archidekt API | Public decklists per commander (`import_archidekt.py`, #148) — training co-occurrence + harness quota statistics |
| cardtrak | Internal collection tracker; additional decklist source |
| XMage source | Java reference implementation; used to cross-check ability pattern extraction |

---

## Operational notes

See `CLAUDE.md` for the full development workflow, two-environment setup
(Docker host for serving, GPU machine for training), training scripts, and
infrastructure lessons learned.
