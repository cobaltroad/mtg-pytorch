"""MTG Commander AI — Streamlit interface."""

import json as _json
import os
import re
import time as _time

import httpx
import pandas as pd
import streamlit as st

API_URL = os.environ.get("API_URL", "http://api:8000")

st.set_page_config(page_title="MTG Commander AI", page_icon="🃏", layout="wide")
st.title("🃏 MTG Commander AI")


# ── Helpers ───────────────────────────────────────────────────────────────────


@st.cache_data(ttl=300)
def search_cards(q: str) -> list[dict]:
    r = httpx.get(f"{API_URL}/cards/search", params={"q": q, "limit": 20}, timeout=10)
    r.raise_for_status()
    return r.json()


@st.cache_data(ttl=300)
def get_commander_decompose(oracle_id: str) -> list[dict]:
    try:
        r = httpx.get(f"{API_URL}/commanders/{oracle_id}/decompose", timeout=10)
        r.raise_for_status()
        return r.json()
    except Exception:
        return []


@st.cache_data(ttl=30)
def list_generated_decks() -> list[dict]:
    try:
        r = httpx.get(f"{API_URL}/decks/generated", timeout=10)
        r.raise_for_status()
        return r.json()
    except Exception:
        return []


@st.cache_data(ttl=60)
def get_generated_deck(filename: str) -> dict | None:
    try:
        r = httpx.get(f"{API_URL}/decks/generated/{filename}", timeout=10)
        r.raise_for_status()
        return r.json()
    except Exception:
        return None


def build_composition_deck(oracle_id: str, ranking: str, games: int) -> dict:
    """Submit a composition build job and poll until done (#150).

    The async job pattern survives long builds without holding one HTTP
    request open for the duration; the deck also persists to history
    server-side regardless of what happens to this page.
    """
    r = httpx.post(
        f"{API_URL}/commanders/{oracle_id}/build/async",
        params={"ranking": ranking, "goldfish_games": games},
        timeout=30,
    )
    r.raise_for_status()
    job_id = r.json()["job_id"]

    deadline = _time.time() + 600
    while _time.time() < deadline:
        _time.sleep(2)
        s = httpx.get(f"{API_URL}/build/jobs/{job_id}", timeout=10)
        s.raise_for_status()
        job = s.json()
        if job["status"] == "done":
            return job["result"]
        if job["status"] == "error":
            raise RuntimeError(job.get("error") or "build failed")
    raise TimeoutError("build did not finish within 10 minutes")


# ── UI constants ──────────────────────────────────────────────────────────────


def _checkpoint_label(checkpoint: str) -> str:
    if checkpoint.startswith("composition/"):
        return "🏗 Composition"
    if checkpoint.startswith("cmd_"):
        return "👑 Commander"
    return "🎯 Phase"


def render_composition(comp: dict) -> None:
    """Render the quota profile, goldfish metrics, and slot breakdown."""
    g = comp.get("goldfish", {})
    m1, m2, m3, m4 = st.columns(4)
    m1.metric(
        "P(commander on time)",
        f"{g.get('p_commander_by_go_live', 0):.2f}",
        f"gate {comp.get('gate', 0):.2f} {'✓' if comp.get('gate_passed') else '✗'}",
    )
    m2.metric("Avg cast turn", f"{g.get('avg_cast_turn', 0):.1f}")
    m3.metric("Keepable hands", f"{g.get('keepable_rate', 0):.0%}")
    d = comp.get("theme_density", {})
    m4.metric(
        "Theme density",
        f"{d.get('commander_edge_rate', 0):.2f}",
        f"pairwise {d.get('pairwise_rate', 0):.3f}",
    )

    for w in comp.get("warnings", []):
        st.warning(w)

    profile = comp.get("profile", {})
    go = profile.get("go_live_turn", {})
    st.caption(f"Go live: turn {go.get('turn', '?')} — {go.get('because', '')}")

    quota_rows = []
    for name, q in profile.get("quotas", {}).items():
        extra = f" (≤{q['max_mv']} MV)" if name == "ramp" and "max_mv" in q else ""
        extra = f" ({q.get('engines')} engines / {q.get('spells')} spells)" if name == "draw" else extra
        quota_rows.append(
            {"quota": name + extra, "count": q["count"], "because": q["because"]}
        )
    st.dataframe(pd.DataFrame(quota_rows), use_container_width=True, hide_index=True)

    reqs = profile.get("pip_requirements", [])
    if reqs:
        st.caption(
            "Commander castability: "
            + "; ".join(
                f"{r['pips']}×{{{r['color']}}} by T{r['by_turn']} → {r['sources']} sources"
                for r in reqs
            )
        )

    with st.expander("Slot breakdown"):
        for slot, names in comp.get("breakdown", {}).items():
            if names:
                st.markdown(f"**{slot}** ({len(names)}): " + ", ".join(names))
        basics = comp.get("basics", {})
        if basics:
            st.markdown("**basics**: " + ", ".join(f"{c}×{n}" for c, n in basics.items()))


# ── Shared deck display ───────────────────────────────────────────────────────


def render_deck(deck: dict) -> None:
    """Render the full deck view. Accepts the deck result dict from the API."""
    st.success(f"Deck generated with checkpoint `{deck['checkpoint']}`")
    st.markdown(f"**Commander:** {deck['commander']['name']}")

    if deck.get("composition"):
        render_composition(deck["composition"])

    _safe_name = re.sub(r"[^\w]", "_", deck["commander"]["name"])
    _dl_cols = st.columns(2)
    _dl_cols[0].download_button(
        "⬇ Download deck (JSON)",
        data=_json.dumps(deck, indent=2, default=str),
        file_name=f"{_safe_name}.json",
        mime="application/json",
    )
    _deck_lines = [f"Commander\n1 {deck['commander']['name']}\n\nDeck"]
    for _c in deck["cards"]:
        _deck_lines.append(f"{_c.get('count', 1)} {_c['name']}")
    _dl_cols[1].download_button(
        "⬇ Download deck (text)",
        data="\n".join(_deck_lines),
        file_name=f"{_safe_name}.txt",
        mime="text/plain",
    )

    rows = [
        {
            "count": c.get("count", 1),
            "name": c["name"],
            "type_line": c.get("type_line", ""),
            "mana_cost": c.get("mana_cost", ""),
            "cmc": c.get("cmc", ""),
            "score": round(s, 4),
        }
        for c, s in zip(deck["cards"], deck["scores"])
    ]
    df = pd.DataFrame(rows).sort_values("score", ascending=False).reset_index(drop=True)
    st.dataframe(
        df,
        use_container_width=True,
        height=600,
        column_config={
            "count": st.column_config.NumberColumn("#", width="small"),
            "score": st.column_config.NumberColumn("Score", format="%.4f"),
        },
    )


# ── Layout ────────────────────────────────────────────────────────────────────

tab_deck, tab_history = st.tabs(["Deck Builder", "Generated Decks"])

# ── Deck Builder tab ──────────────────────────────────────────────────────────

with tab_deck:
    st.subheader("Commander deck builder")
    st.markdown(
        "Search for a commander, review its decompose signals, and build a "
        "99-card deck with the composition engine."
    )

    cmd_query = st.text_input("Commander name", placeholder="Atraxa, Praetors' Voice")
    commander = None

    if cmd_query:
        with st.spinner("Searching…"):
            candidates = search_cards(cmd_query)
        if candidates:
            options = {f"{c['name']} — {c.get('type_line', '')}": c for c in candidates}
            choice = st.selectbox("Select commander", list(options.keys()))
            commander = options[choice]

    # ── Commander decompose signals ───────────────────────────────────────────
    if commander:
        _signals = get_commander_decompose(str(commander["oracle_id"]))
        with st.expander(f"Decompose signals: {commander['name']}", expanded=True):
            # Oracle text
            _oracle = (commander.get("oracle_text") or "").strip()
            if _oracle:
                st.markdown(
                    "<div style='font-size:0.85em; color:#ccc; white-space:pre-wrap; "
                    "border-left:3px solid #555; padding-left:0.75em; margin-bottom:0.75em;'>"
                    + _oracle.replace("\n", "<br>")
                    + "</div>",
                    unsafe_allow_html=True,
                )
            if not _signals:
                st.info(
                    "No decompose signals found. Run the decompose pipeline stage first."
                )
            else:
                # Render signals as a two-column table:
                #   col 1 — signal label + matched phrase
                #   col 2 — deck key (backtick) + side badge + deck label
                _sig_col1, _sig_col2 = st.columns([2, 2])
                with _sig_col1:
                    st.caption("Signal")
                with _sig_col2:
                    st.caption("Deck needs")
                for sig in _signals:
                    phrase = (
                        f'  — `"{sig["raw_text"]}"` ' if sig.get("raw_text") else ""
                    )
                    deck_keys = sig.get("deck_keys") or []
                    deck_labels = sig.get("deck_labels") or []
                    side = sig.get("side")
                    _c1, _c2 = st.columns([2, 2])
                    with _c1:
                        st.markdown(f"**{sig['ability_name']}**{phrase}")
                    with _c2:
                        if deck_keys and side:
                            side_badge = "📤" if side == "producer" else "📥"
                            lines = [
                                f"{side_badge} `{dk}` — {dl}"
                                for dk, dl in zip(deck_keys, deck_labels)
                            ]
                            st.markdown("  \n".join(lines))
                        else:
                            st.markdown("—")

    # ── Composition build (docs/composition-first-plan.md W5) ────────────────
    if commander:
        st.divider()
        st.subheader("Composition build")
        st.markdown(
            "Deterministic quotas (lands / ramp / draw / interaction / protection) "
            "derived from the commander; models only rank cards *within* each quota."
        )
        _b_col1, _b_col2, _b_col3 = st.columns([1, 1, 2])
        with _b_col1:
            _ranking = st.radio(
                "Ranking", ["model", "heuristic"], horizontal=True,
                help="model = Phase 1/2 bilinear re-ranked pools; "
                "heuristic = deterministic baseline",
            )
        with _b_col2:
            _games = st.select_slider(
                "Goldfish games", options=[100, 300, 500, 1000], value=300,
                help="Monte Carlo iterations for castability metrics",
            )
        with _b_col3:
            st.markdown("&nbsp;", unsafe_allow_html=True)
            _do_build = st.button("🏗 Build 99-card deck", type="primary")

        if _do_build:
            with st.spinner("Building deck (quota fill + mana base + goldfish)…"):
                try:
                    _built = build_composition_deck(
                        str(commander["oracle_id"]), _ranking, _games
                    )
                except httpx.HTTPStatusError as e:
                    st.error(f"Build failed ({e.response.status_code}): {e.response.text}")
                    _built = None
                except Exception as e:
                    st.error(f"Build failed: {e}")
                    _built = None
            if _built:
                st.session_state["last_deck_filename"] = _built.get("deck_filename", "")
                list_generated_decks.clear()
                render_deck(_built)
                st.info(
                    "Deck saved — also available in the **Generated Decks** tab."
                )

    # (The Phase 3 CommanderScorer candidate table and checkpoint picker
    #  lived here until #151 — the composition build above replaces them.)


# ── Generated Decks tab ───────────────────────────────────────────────────────

with tab_history:
    st.subheader("Generated decks")

    decks_list = list_generated_decks()

    if not decks_list:
        st.info("No generated decks on record.")
    else:

        def _deck_label(d: dict) -> str:
            badge = _checkpoint_label(d.get("checkpoint", ""))
            return f"{d['commander']}  [{badge}]  —  {d['filename']}  ({d['card_count']} cards)"

        options_map = {_deck_label(d): d["filename"] for d in decks_list}
        labels = list(options_map.keys())

        default_idx = 0
        last_fn = st.session_state.get("last_deck_filename")
        if last_fn:
            for i, lbl in enumerate(labels):
                if last_fn in lbl:
                    default_idx = i
                    break

        _hist_col1, _hist_col2 = st.columns([3, 1])
        with _hist_col1:
            chosen_label = st.selectbox("Select a deck", labels, index=default_idx)
        with _hist_col2:
            st.markdown("&nbsp;", unsafe_allow_html=True)
            _compare_mode = st.checkbox("Compare two decks")

        if st.button("Refresh list"):
            list_generated_decks.clear()
            get_generated_deck.clear()
            st.rerun()

        chosen_filename = options_map[chosen_label]

        if _compare_mode:
            _other_labels = [l for l in labels if l != chosen_label]
            if not _other_labels:
                st.warning("Need at least two decks to compare.")
                _compare_mode = False
            else:
                chosen_label_b = st.selectbox(
                    "Compare with", _other_labels, key="compare_b"
                )
                chosen_filename_b = options_map[chosen_label_b]

        st.divider()

        if _compare_mode:
            deck_a = get_generated_deck(chosen_filename)
            deck_b = get_generated_deck(chosen_filename_b)
            if deck_a is None or deck_b is None:
                st.error("Could not load one or both decks.")
            else:
                col_a, col_b = st.columns(2)
                with col_a:
                    st.markdown(
                        f"### {_checkpoint_label(deck_a.get('checkpoint', ''))}"
                    )
                    render_deck(deck_a)
                with col_b:
                    st.markdown(
                        f"### {_checkpoint_label(deck_b.get('checkpoint', ''))}"
                    )
                    render_deck(deck_b)
        else:
            deck = get_generated_deck(chosen_filename)
            if deck is None:
                st.error("Could not load deck. The file may have been deleted.")
            else:
                render_deck(deck)
