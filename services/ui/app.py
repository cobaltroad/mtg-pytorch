"""MTG Commander AI — Streamlit interface."""

import json as _json
import os
import re

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


@st.cache_data(ttl=120)
def score_candidates(oracle_id: str, checkpoint: str = "phase3_best") -> list[dict]:
    r = httpx.get(
        f"{API_URL}/commanders/{oracle_id}/candidates",
        params={"checkpoint": checkpoint},
        timeout=60,
    )
    r.raise_for_status()
    return r.json()


@st.cache_data(ttl=300)
def analyze_commander(oracle_id: str) -> dict | None:
    try:
        r = httpx.get(f"{API_URL}/commanders/{oracle_id}/analyze", timeout=10)
        r.raise_for_status()
        return r.json()
    except Exception:
        return None


@st.cache_data(ttl=30)
def list_generated_decks() -> list[dict]:
    try:
        r = httpx.get(f"{API_URL}/decks/generated", timeout=10)
        r.raise_for_status()
        return r.json()
    except Exception:
        return []


@st.cache_data(ttl=60)
def list_checkpoints() -> list[dict]:
    try:
        r = httpx.get(f"{API_URL}/checkpoints", timeout=10)
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


# ── UI constants ──────────────────────────────────────────────────────────────

_CONFIDENCE_ICONS = {"high": "✅", "medium": "⚠️", "low": "❓", "unknown": "❓"}
_GENERATION_CONF_ICONS = {"high": "🟢", "medium": "🟡", "low": "🟠", "none": "🔴"}


def _checkpoint_label(checkpoint: str) -> str:
    if checkpoint.startswith("cmd_"):
        return "👑 Commander"
    return "🎯 Phase"


# ── Shared deck display ───────────────────────────────────────────────────────

def render_deck(deck: dict) -> None:
    """Render the full deck view. Accepts the deck result dict from the API."""
    st.success(f"Deck generated with checkpoint `{deck['checkpoint']}`")
    st.markdown(f"**Commander:** {deck['commander']['name']}")

    _safe_name = re.sub(r"[^\w]", "_", deck['commander']['name'])
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
    st.subheader("Commander candidate scoring")
    st.markdown(
        "Search for a commander to see all color-identity-legal cards ranked by "
        "commander-card fit score (Phase 3 CommanderScorer)."
    )

    cmd_query = st.text_input("Commander name", placeholder="Atraxa, Praetors' Voice")
    commander = None

    if cmd_query:
        with st.spinner("Searching…"):
            candidates = search_cards(cmd_query)
        if candidates:
            options = {f"{c['name']} — {c.get('type_line','')}": c for c in candidates}
            choice = st.selectbox("Select commander", list(options.keys()))
            commander = options[choice]

    # ── Commander Analysis panel ──────────────────────────────────────────────
    _analysis: dict | None = None
    if commander:
        _analysis = analyze_commander(str(commander["oracle_id"]))

        conf_label = _analysis.get("generation_confidence", "none") if _analysis else "none"
        conf_icon = _GENERATION_CONF_ICONS.get(conf_label, "❓")

        with st.expander(
            f"Commander Analysis: {commander['name']}  {conf_icon} generation confidence: {conf_label}",
            expanded=True,
        ):
            if _analysis is None:
                st.warning("Could not fetch commander analysis (API unavailable).")
            else:
                colors = " / ".join(_analysis.get("color_identity") or []) or "Colorless"
                st.markdown(f"**Colors:** {colors}")

                hint = _analysis.get("archetype_hint")
                if hint:
                    st.markdown(f"**Inferred deck goal:** {hint}")

                signals = _analysis.get("signals", [])
                if signals:
                    st.markdown("**Detected signals:**")
                    for sig in signals:
                        conf = sig.get("confidence", "unknown")
                        icon = _CONFIDENCE_ICONS.get(conf, "❓")
                        boost_note = " *(boost applied)*" if sig.get("boost_applied") else ""
                        phrase = sig.get("phrase", "")
                        label = sig.get("label", "")
                        sig_type = sig.get("signal_type", "")
                        st.markdown(
                            f"  {icon} **{sig_type}**: {label}"
                            f'  — `"{phrase}"`  [confidence: {conf}]{boost_note}'
                        )
                else:
                    st.info("No signals detected from oracle text.")

                gaps = _analysis.get("gaps", [])
                if gaps:
                    st.markdown("**Gaps** *(mechanics the parser couldn't fully interpret):*")
                    for gap in gaps:
                        st.markdown(f"  ❓ {gap}")

    # ── Advanced options ──────────────────────────────────────────────────────
    _chosen_checkpoint = "phase3_best"
    if commander:
        with st.expander("Advanced options", expanded=False):
            _available = list_checkpoints()
            _default_ckpt = "phase3_best"
            if _available:
                _ckpt_options = [c["name"] for c in _available]
                _default_ckpt_idx = (
                    _ckpt_options.index(_default_ckpt)
                    if _default_ckpt in _ckpt_options
                    else 0
                )
                _chosen_checkpoint = st.selectbox(
                    "Checkpoint",
                    _ckpt_options,
                    index=_default_ckpt_idx,
                    help="Select a checkpoint to use for candidate scoring.",
                )
            else:
                _chosen_checkpoint = _default_ckpt
                st.caption(f"Checkpoint: `{_chosen_checkpoint}` (not yet uploaded)")

    # ── Candidate scoring table ───────────────────────────────────────────────
    if commander:
        oracle_id = str(commander["oracle_id"])
        with st.spinner("Scoring candidates…"):
            try:
                results = score_candidates(oracle_id, _chosen_checkpoint)
            except httpx.HTTPStatusError as e:
                st.error(f"Scoring failed ({e.response.status_code}): {e.response.text}")
                results = []
            except Exception as e:
                st.error(f"Scoring failed: {e}")
                results = []

        if results:
            st.caption(f"{len(results)} color-identity-legal candidates scored")
            df = pd.DataFrame([
                {
                    "name": r["name"],
                    "type_line": r.get("type_line") or "",
                    "mana_cost": r.get("mana_cost") or "",
                    "cmc": r.get("cmc"),
                    "score": round(r["score"], 4),
                }
                for r in results
            ])
            st.dataframe(
                df,
                use_container_width=True,
                height=700,
                column_config={
                    "score": st.column_config.NumberColumn("Score", format="%.4f"),
                    "cmc": st.column_config.NumberColumn("CMC", format="%.0f"),
                },
            )


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
                chosen_label_b = st.selectbox("Compare with", _other_labels, key="compare_b")
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
                    st.markdown(f"### {_checkpoint_label(deck_a.get('checkpoint', ''))}")
                    render_deck(deck_a)
                with col_b:
                    st.markdown(f"### {_checkpoint_label(deck_b.get('checkpoint', ''))}")
                    render_deck(deck_b)
        else:
            deck = get_generated_deck(chosen_filename)
            if deck is None:
                st.error("Could not load deck. The file may have been deleted.")
            else:
                render_deck(deck)
