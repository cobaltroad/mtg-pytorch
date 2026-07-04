# Composition-First — Next Steps

**Status:** backlog draft · 2026-07-04 · follows the merge of PR #132
(`docs/composition-first-plan.md` W1–W6, all complete)

The engine is live end-to-end: derived quotas → ranked pools → mana-base
solver → goldfish gate → API/UI, with a passing 20-commander regression
harness.  What remains is not architecture — it is **signal coverage,
in-slot card quality, and simulation fidelity**, in that order of leverage.
Each item below is written to be liftable into a GitHub issue as-is.

Every item states its **verification** — almost always the W6 harness
(`scripts/eval_harness.py`) or the A/B metrics in `scripts/build_deck.py`,
so improvements are measured, not asserted.

---

## Track 1 — Theme signal coverage (highest leverage)

The harness shows correct quotas but `commander_edge_rate = 0` for any
commander whose decompose keys have no consumer SQL.  These decks are
legal, castable, and *themeless*.

### 1.1 Consumer SQL for the graveyard family
Muldrotha, Karador, Meren-adjacent keys (`graveyard_from_play`,
`graveyard_payoff`, `unearth_encore`) fire on commanders but have no entry
in `PATTERN_KEY_TO_CONSUMER_SQL`.  Add consumer SQL (self-mill, recursion,
plays-from-graveyard enablers).
**Verify:** Muldrotha/Karador `commander_edge_rate` goes 0 → >0.8 in the
harness; theme slots stop backfilling.

### 1.2 Consumer SQL for `high_mv_payoff` and per-color cast triggers
Kozilek (`high_mv_payoff`) wants big spells; Niv-Mizzet
(`cast_trigger_blue/red`) wants instants/sorceries in those colors.  Both
exist as decompose keys with no consumer side.
**Verify:** same metric, same commanders.

### 1.3 `activated_engine` decompose pattern (Yisan)
No ORACLE_PATTERNS entry matches repeatable activated-ability engines
("{X}, {T}, Put a verse counter on ~: search…"), so Yisan-style commanders
fire zero signals → wrong protection quota (2 instead of 5) and empty
theme pool.  Add the pattern + consumer SQL; re-run `decompose_commanders`.
**Verify:** `eval_profile "Yisan"` shows engine-tier protection;
`eval_decomposition --no-signals` list shrinks.

### 1.4 Systematic gap sweep
`eval_decomposition --no-signals` + harness `commander_edge_rate = 0` is a
measurable worklist.  Burn it down key by key (the pre-merge
`token_generator`/`proliferate_matters`/`trigger_doubling` commits are the
template for these PRs).
**Verify:** count of zero-signal commanders and zero-density golden decks,
tracked in each PR description.

### 1.5 Keep DB-materialized decompose artifacts fresh
The API build path reads `card_abilities (source='decompose')` and
`decomposed_candidates` edges; the ingest script computes signals live.
After any pattern change the two diverge until `decompose_commanders` +
`compute_commander_value_synergy` re-run.  Either fold both stages into
`--stage process` or add a staleness check (compare pattern-key set in
code vs distinct `trigger_event` values in DB) to the harness.
**Verify:** harness warns when the API's signal set is stale.

---

## Track 2 — In-slot card quality

Quotas are right; the *cards inside them* are only as good as pool ranking.

### 2.1 Retrain Phase 1/2 on cleaned, color-filtered edges
Two known improvements are sitting un-trained: the sweeper-SQL false-positive
fix (feeds Phase 1 staple anchors) and the long-standing color-identity
edge filtering from `docs/TODO.md` (cross-color false positives pollute
Phase 2's signal).  Retraining sequence: implement edge filtering →
`--stage process` → `export_dataset` → `download_dataset.ps1` on the GPU
machine → Phase 1 then Phase 2 → upload `phase1_best` /
`phase2_bilinear_best` (+ `phase2_best`) → API hot-swaps.
**Verify:** A/B theme metrics in `build_deck.py` vs current checkpoints;
harness stays green.

### 2.2 Re-embed the ~950 unembedded cards
`--stage embed_cards` after each download; those cards are invisible to
model ranking until then (they tail-rank heuristically).  Candidate for
folding into a scheduled download+embed job.
**Verify:** `card_embeddings` count == commander-legal card count.

### 2.3 Popularity prior for heuristic ranking
Alphabetical tie-breaking surfaces junk ("Aang's Journey" as ramp).  A
coarse staple/popularity prior (EDHREC rank, or even ban-adjacent
"format staple" tiers curated per pool) would fix the heuristic baseline
and give the model ranker a better fallback tail.
**Verify:** human spot-check of ramp/draw picks for 5 golden commanders;
no castability regression in the harness.

### 2.4 Wincon audit (planned in W-plan Layer 3, never implemented)
Assert the theme slots contain ≥2 cards role-tagged `win_condition`
(`services/api/ops/card_roles.py` detection exists); force-include from a
wincon pool otherwise.
**Verify:** new hard check in `evaluation.py` + harness stays green.

---

## Track 3 — Simulation fidelity (goldfish + mana base)

### 3.1 Model commander cost reduction properly
Karador currently gets a flat `gate_relax = 0.15` hack.  Simulate the
reduction ("costs {1} less for each creature card in your graveyard" ≈
turn-indexed discount schedule) and delete the relax constant.
**Verify:** Karador passes its *unrelaxed* gate.

### 3.2 Use MDFC lands in the mana base
`card_facts.is_mdfc_land` is computed and stored but the builder never
consumes it.  Spell-front MDFCs should count as fractional lands (land
count credit) and enter the goldfish as playable land drops.
**Verify:** builds include Malakir Rebirth-class cards when on-color; land
credit reflected in `lands.because`; harness green.

### 3.3 Fetch-aware colored-source counting
Fetches count as 0 colored sources in the solver today (empty
`produced_mana`).  Count them toward the colors of the basics they can
find.
**Verify:** multicolor golden decks (Atraxa) hit pip minimums with fewer
warnings.

### 3.4 Colored costs for ramp in the goldfisher
Ramp casts check total mana only — a {G} dork is "castable" off a Swamp.
Check pips for ramp casts too.
**Verify:** goldfish P values dip slightly but honestly; gate floors
recalibrated if needed (they are documented calibration constants).

### 3.5 Pip-offender swap in the feedback loop
The loop only converts theme slots to lands.  The plan's original design
also swapped the worst pip-offenders (e.g. a {B}{B}{B} theme card in a
3-color deck) for on-curve alternatives before adding lands.
**Verify:** Atraxa-class decks pass gates at lower land counts.

---

## Track 4 — Product & data

### 4.1 Partner commanders
Two-card commanders (identity union, both castable-on-time) are
unsupported end-to-end: profile derivation, builder, imports
(`import_decklists.py` still skips single-slash partners).
**Verify:** golden set gains Rograkh+Silas or Thrasios+Tymna; harness green.

### 4.2 More decklists (still matters)
The W6 human-distribution comparison runs on 69 complete decks — thin.
The old TODO's priority-theme list (aristocrats, tokens, counters, …)
still applies; decklists now serve two masters: co-occurrence training
signal *and* harness statistics.
**Verify:** `human_distributions` deck count; tighter min/max bands.

### 4.3 Deck browser feedback loop, composition-aligned
The planned upload → parse → vote → amend loop (see memory / old deck
browser work) should now annotate *slot assignments and theme picks*, not
free-form roles — votes become labels for within-slot ranking.
**Verify:** design doc first; this is a feature-sized item.

### 4.4 Async build endpoint
`POST /commanders/{id}/build` is synchronous (~10–15 s).  Fine for the UI
spinner today; wrap in the jobs pattern if timeouts appear or goldfish
games increase.
**Verify:** UI builds under load without 504s.

---

## Track 5 — Cleanup & infrastructure

### 5.1 Retire Phase 3/4 for real
The plan retires them; the code still carries `CommanderScorer`, the
`/commanders/{id}/candidates` phase3 path, the 30/70 blend, and the
`phase3_best`/`phase4_best` checkpoints.  Once the composition path has
earned trust in daily use: delete the scoring path, park the checkpoints,
simplify the UI's checkpoint picker.
**Verify:** API surface shrinks; UI unaffected; docs updated.

### 5.2 Consolidate model definitions (3 copies)
`CardEncoder`/`BilinearSynergyHead` exist in `trainer/train.py`,
`api/ops/model.py`, and `shared/composition/ranking.py`.  Move to
`shared/` (the empty `models/` dir claim in CLAUDE.md should be fixed or
the dir removed at the same time).
**Verify:** all three import sites point at one definition; checkpoints
still load.

### 5.3 Fix or delete the stale test files
`test_land_tags`, `test_import_utils`, `test_roles`,
`test_synergy_expansion` fail collection; `test_tribal_typeline_synergy`
has 25 pre-existing failures.  They drifted from refactors long before the
composition work; decide per-file and get `pytest tests/` green with no
ignore flags.
**Verify:** bare `pytest tests/` passes in the ingest container.

### 5.4 CI for the pure suite
The 94 composition tests are DB-free and torch-free — they can run in a
plain GitHub Actions job (pytest + the `shared/` path).  The harness stays
host-run (needs DB + checkpoints); wire its exit code into a pre-merge
checklist instead.
**Verify:** Actions badge; failing pure test blocks merge.

### 5.5 Rewrite README around composition-first
The README still describes the Phase 3/4 pipeline as the main path;
composition-first is the architecture now.  Rewrite the "Approach
overview" and "Deck building at inference" sections; fold the Known Gaps
into issue links once the backlog exists.
**Verify:** a new reader lands on the current architecture.

---

## Suggested ordering

1. **1.3 + 1.1 + 1.2** — theme coverage: biggest visible deck-quality jump,
   pure SQL/regex work, no retraining.
2. **2.2** (re-embed) and **1.5** (freshness) — cheap hygiene enabling
   everything else.
3. **3.2 + 3.3** — mana-base fidelity, uses data W1 already computed.
4. **2.1** — the one retraining cycle, after edge cleanup lands.
5. **4.1** (partners) and **2.4** (wincon audit) — feature-sized.
6. Track 5 cleanup interleaved as palate cleansers; **5.5** after the
   backlog exists so the README can link issues.

Nothing in Tracks 1, 3, 4, or 5 requires GPU-machine retraining; only 2.1
does, and its full sequence is spelled out inline.
