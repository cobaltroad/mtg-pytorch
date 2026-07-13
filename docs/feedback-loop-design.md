# Deck Feedback Loop — Design (#149)

**Status:** design for review · 2026-07-12 · no implementation issues cut yet

## Goal

Close the loop between generated decks and the learned ranker: human votes
on deck contents become supervised labels for **within-slot ranking** — the
only decision the composition architecture leaves to the model.  The
original vision (2026-03: upload → parse → vote → amend → train) predates
composition-first; this design re-grounds it in the current vocabulary.

## What already exists

| Asset | State |
|---|---|
| `ops/deck_browser.py` — `list_decks`, `get_deck_with_roles`, `apply_votes`, `amend_with_votes` | **Dormant** — built 2026-03, not wired into `main.py`; votes stored in `decks.metadata['votes']` keyed by free-form role (`card_role:<id>:<role>`) and archetype |
| `ops/card_roles.py` role/archetype detection | Used by `detect_archetype` in imports; the loose role vocabulary (`win_condition` = any trample card) was already rejected as a hard-check basis in #141 |
| Generated decks (`DECK_SAVE_DIR` JSON) | Every card carries `slot` (ramp/draw/theme/wincon/…); the composition block carries `win_path`, quota rationales, `theme_keys` via edges |
| `decks` table | 501 imported human decks (#148) |

## Design decisions

### 1. Generated decks are the primary annotation surface

The March design annotated *imported* decks with detected roles.  Post
composition-first, **generated decks are strictly better vote targets**:
every card already has a slot assignment, a "because", and a known
commander context.  A vote is unambiguous: *"Gray Merchant in this Wilhelt
deck: good pick / bad pick."*  Imported-deck browsing stays as a secondary
surface (§6).

### 2. Two vote kinds, two very different sinks

- **Fit votes** (±1 on a card in a commander's deck) → **model labels**.
  These are exactly (commander, card, label) triples — the supervised form
  of the bilinear `decomposed_candidates` relation.  Sink: training pairs
  at export (§4).
- **Slot disputes** ("this isn't ramp") → **diagnostics, not labels**.
  Pool membership is deterministic SQL; a slot dispute means a staple SQL
  or pattern bug.  Sink: a review queue (initially: a harness-style report
  listing disputed pool memberships), never the model.

This split is the core correction to the March design, which funneled all
votes into `card_abilities` role tags — mixing rule-fixes with preference
signal.

### 3. Storage: a real table, not a metadata blob

```sql
CREATE TABLE card_votes (
    id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    commander_id  UUID NOT NULL REFERENCES cards(id),
    card_id       UUID NOT NULL REFERENCES cards(id),
    vote          SMALLINT NOT NULL CHECK (vote IN (-1, 1)),
    kind          TEXT NOT NULL DEFAULT 'fit',   -- 'fit' | 'slot'
    slot          TEXT,                          -- slot at vote time (context)
    deck_ref      TEXT,                          -- generated filename or decks.id
    created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (commander_id, card_id, kind, deck_ref)
);
```

Votes aggregate across decks per (commander, card) — the model label is
`sign(sum(vote))` with `|sum|` as confidence weight.  The existing
`decks.metadata['votes']` blob is read-once-migrated, then retired with
`apply_votes`/`amend_with_votes` (they were never wired up).

### 4. Training integration

`export_dataset` gains a `vote_pairs` section: `(commander_id, card_id,
label, weight)`.  Phase 2 bilinear training adds them to the
`decomposed_candidates` relation batch with per-pair weights — positive
votes as extra positives, negative votes as **hard negatives** (currently
the scarcest resource; random negatives are trivially wrong).  A few
hundred votes is already meaningful at this granularity because the
relation head has only 256×256 parameters per relation.

Verification loop: harness gains a `vote_agreement` metric — of the votes
on record, what fraction does a fresh build agree with (voted-up cards
included, voted-down excluded)?  Measured before/after each retrain.

### 5. API + UI (phase 1 scope)

- `POST /decks/generated/{filename}/votes` — `[{card_name, vote, kind}]`;
  resolves names against the saved deck, upserts `card_votes` rows with
  the commander and slot context.
- `GET /commanders/{oracle_id}/votes` — aggregated per card (for showing
  prior votes in the UI and for export).
- UI: in the Generated Decks view, a 👍/👎 toggle per card row.  No
  free-text, no role pickers — two taps max, per the original insight
  that structured tags beat prose.

### 6. Later phases (separate issues, cut after this doc is approved)

1. **Vote capture** — table + endpoints + UI toggles (§3, §5).
2. **Export + training weights** — vote_pairs in the artifact; retrain
   A/B'd on vote_agreement (§4).
3. **Slot-dispute report** — surfacing 'slot' votes as a pool-SQL review
   queue, harness-style.
4. **Imported-deck browsing** — re-skin `get_deck_with_roles` onto
   composition vocabulary: show each imported deck bucketed by the
   builder's pool memberships, votable the same way.  (This is where the
   dormant deck_browser code gets revived or deleted.)
5. **Amend pass** — "rebuild this deck honoring my votes": votes become
   per-build pool overrides (pin voted-up, exclude voted-down) — cheap to
   implement since pools are just ranked lists.

## Open questions for review

- **Single-user assumption?**  The schema allows multi-user later (add a
  `voter` column) but phase 1 assumes one operator.
- **Vote decay** — metagame shifts; do old votes age out at export
  (weight × e^(−age))?  Proposed: ignore until vote volume makes it real.
- **Should slot disputes auto-open GitHub issues** (like the #136 sweep
  workflow) or stay a report?  Proposed: report first.
