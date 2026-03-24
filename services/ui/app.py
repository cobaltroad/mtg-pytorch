"""MTG Commander AI — Streamlit interface."""

import json as _json
import os
import re
import time

import httpx
import pandas as pd
import streamlit as st

API_URL          = os.environ.get("API_URL", "http://api:8000")

st.set_page_config(page_title="MTG Commander AI", page_icon="🃏", layout="wide")
st.title("🃏 MTG Commander AI")


# ── Helpers ───────────────────────────────────────────────────────────────────

@st.cache_data(ttl=300)
def search_cards(q: str) -> list[dict]:
    r = httpx.get(f"{API_URL}/cards/search", params={"q": q, "limit": 20}, timeout=10)
    r.raise_for_status()
    return r.json()


def submit_deck_job(
    oracle_id: str,
    checkpoint: str = "latest",
    boost_overrides: list[str] | None = None,
    synergy_alpha: float = 0.4,
) -> str:
    """Submit a generation job and return the job_id."""
    r = httpx.post(
        f"{API_URL}/decks/generate",
        json={
            "commander_oracle_id": oracle_id,
            "checkpoint": checkpoint,
            "boost_overrides": boost_overrides or [],
            "synergy_alpha": synergy_alpha,
        },
        timeout=15,
    )
    r.raise_for_status()
    return r.json()["job_id"]


def poll_job(job_id: str) -> dict:
    """Fetch current job status."""
    r = httpx.get(f"{API_URL}/decks/jobs/{job_id}", timeout=10)
    r.raise_for_status()
    return r.json()


@st.cache_data(ttl=300)
def analyze_commander(oracle_id: str) -> dict | None:
    """Call the analyze endpoint for a commander (cached 5 min)."""
    try:
        r = httpx.get(f"{API_URL}/commanders/{oracle_id}/analyze", timeout=10)
        r.raise_for_status()
        return r.json()
    except Exception:
        return None


@st.cache_data(ttl=30)
def list_generated_decks() -> list[dict]:
    """List previously generated decks from the API (cached 30 s)."""
    try:
        r = httpx.get(f"{API_URL}/decks/generated", timeout=10)
        r.raise_for_status()
        return r.json()
    except Exception:
        return []


@st.cache_data(ttl=60)
def list_checkpoints() -> list[dict]:
    """List available checkpoints from the API (cached 60 s)."""
    try:
        r = httpx.get(f"{API_URL}/checkpoints", timeout=10)
        r.raise_for_status()
        return r.json()
    except Exception:
        return []


@st.cache_data(ttl=60)
def get_generated_deck(filename: str) -> dict | None:
    """Fetch a specific generated deck by filename (cached 60 s)."""
    try:
        r = httpx.get(f"{API_URL}/decks/generated/{filename}", timeout=10)
        r.raise_for_status()
        return r.json()
    except Exception:
        return None


# ── UI constants ──────────────────────────────────────────────────────────────

_CONFIDENCE_ICONS = {"high": "✅", "medium": "⚠️", "low": "❓", "unknown": "❓"}
_GENERATION_CONF_ICONS = {"high": "🟢", "medium": "🟡", "low": "🟠", "none": "🔴"}

_PATH_BADGES = {
    "compositional": "🔬 Compositional",
    "commander": "👑 Commander",
    "co-occurrence": "📊 Co-occurrence",
}

_LATEST_CHECKPOINT = {
    "Co-occurrence": "phase4_best",
    "Compositional": "comp_phase4_best",
}


def _training_path_from_checkpoint(checkpoint: str) -> str:
    if checkpoint.startswith("comp_"):
        return "compositional"
    if checkpoint.startswith("cmd_"):
        return "commander"
    return "co-occurrence"


# ── Shared deck display ───────────────────────────────────────────────────────

def render_deck(deck: dict) -> None:
    """Render the full deck view. Accepts the deck result dict from the API."""
    st.success(f"Deck generated with checkpoint `{deck['checkpoint']}`")
    st.markdown(f"**Commander:** {deck['commander']['name']}")

    # ── Download buttons ──────────────────────────────────────────
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

    # ── Context seed section ──────────────────────────────────────
    context_cards = deck.get("context_cards", [])
    is_proxy = deck.get("proxy_context", False)
    if context_cards and not is_proxy:
        with st.expander(f"Context seed ({len(context_cards)} archetype staples used to prime the decoder)"):
            st.write(", ".join(context_cards))
    elif context_cards and is_proxy:
        with st.expander(
            f"⚠️ Proxy context ({len(context_cards)} staples from similar commanders — "
            "no training decks exist for this commander)",
            expanded=True,
        ):
            st.info(
                f"No decklists have been imported for **{deck['commander']['name']}**. "
                "The decoder was seeded with staples from the most embedding-similar "
                "commanders that *do* have training data. "
                "Results will improve once you import decklists for this commander."
            )
            st.write(", ".join(context_cards))
    else:
        st.warning(
            f"No training decks found for **{deck['commander']['name']}** "
            f"and no similar commanders with training data were found. "
            "The model is flying blind — results will be poor."
        )

    # ── Deck signals ─────────────────────────────────────────────
    dsig = deck.get("deck_signals", {})
    if dsig:
        with st.expander("Deck signals (scoring inputs)", expanded=False):
            c1, c2 = st.columns(2)
            c1.markdown(f"**Wants attack:** {'✅' if dsig.get('wants_attack') else '❌'}")
            tribal = dsig.get("tribal_types", [])
            c1.markdown(f"**Tribal types:** {', '.join(tribal) if tribal else '—'}")
            c2.markdown(f"**Colors:** {' '.join(dsig.get('real_colors', [])) or 'colorless'}")
            boosts = dsig.get("active_boosts", [])
            c2.markdown(f"**Active boosts:** {', '.join(boosts) if boosts else '—'}")

    # ── Role breakdown ────────────────────────────────────────────
    archetype = deck.get("archetype", "")
    win_conditions = deck.get("win_conditions", [])
    if archetype:
        arch_label = archetype
        if win_conditions:
            arch_label += "  —  win cons: " + ", ".join(f"`{w}`" for w in win_conditions)
        st.markdown(f"**Archetype:** `{arch_label}`")

    role_counts = deck.get("role_counts", {})
    if role_counts:
        rc_cols = st.columns(len(role_counts))
        for col, (role, cnt) in zip(rc_cols, sorted(role_counts.items())):
            col.metric(role, cnt)

    # ── Deck summary stats ────────────────────────────────────────
    land_count = sum(
        c.get("count", 1) for c in deck["cards"]
        if "Land" in c.get("type_line", "")
    )
    total_count = sum(c.get("count", 1) for c in deck["cards"])
    ramp_count = sum(
        c.get("count", 1) for c in deck["cards"]
        if c.get("is_ramp", False)
    )
    syn_density = deck.get("synergy_density")
    syn_baseline = deck.get("synergy_baseline")
    col1, col2, col3, col4 = st.columns(4)
    col1.metric("Lands", f"{land_count} / {total_count + 1}")  # +1 for commander
    col2.metric("Ramp", str(ramp_count))
    col3.metric("Non-land spells", f"{total_count - land_count} / {total_count + 1}")
    if syn_density is not None:
        delta = round(syn_density - syn_baseline, 4) if syn_baseline else None
        col4.metric(
            "Synergy density",
            f"{syn_density:.4f}",
            delta=f"{delta:+.4f} vs baseline" if delta is not None else None,
        )

    # ── Mana curve bar chart ──────────────────────────────────────
    spells = [
        c for c in deck["cards"]
        if "Land" not in c.get("type_line", "")
    ]
    curve: dict[str, int] = {}
    for c in spells:
        cmc = float(c.get("cmc") or 0)
        label = f"{int(cmc)}+" if cmc >= 6 else str(int(cmc))
        curve[label] = curve.get(label, 0) + c.get("count", 1)
    if curve:
        curve_df = pd.DataFrame(
            sorted(curve.items(), key=lambda x: int(x[0].rstrip("+"))),
            columns=["CMC", "Cards"],
        )
        st.bar_chart(curve_df.set_index("CMC"))

    # ── Deck table sorted by score ────────────────────────────────
    rows = [
        {
            "count": c.get("count", 1),
            "name": c["name"],
            "type_line": c.get("type_line", ""),
            "mana_cost": c.get("mana_cost", ""),
            "cmc": c.get("cmc", ""),
            "roles": " | ".join(
                r["role"] for r in c.get("roles", [])
            ) or "—",
            "effects": " | ".join(
                r["effect_class"] for r in c.get("roles", [])
                if r.get("effect_class")
            ) or "—",
            "score_tags": " | ".join(c.get("score_tags", [])) or "—",
            "score": round(s, 4),
        }
        for c, s in zip(deck["cards"], deck["scores"])
    ]
    df = pd.DataFrame(rows).sort_values(
        ["score"], ascending=False
    ).reset_index(drop=True)
    st.dataframe(
        df,
        use_container_width=True,
        height=600,
        column_config={
            "count": st.column_config.NumberColumn("#", width="small"),
            "roles": st.column_config.TextColumn("roles", width="medium"),
            "effects": st.column_config.TextColumn("effect tags", width="medium"),
            "score_tags": st.column_config.TextColumn("score boosts", width="medium"),
            "score": st.column_config.NumberColumn("Score", format="%.4f"),
        },
    )


# ── Layout ────────────────────────────────────────────────────────────────────

tab_deck, tab_history = st.tabs(["Deck Builder", "Generated Decks"])

# ── Deck Builder tab ─────────────────────────────────────────────────────────

with tab_deck:
    st.subheader("Commander deck builder")
    st.markdown(
        "Search for a commander, then let the model construct the remaining 99 cards."
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

    # ── Commander Analysis panel ───────────────────────────────────────────────
    _analysis: dict | None = None
    _boost_overrides: list[str] = []
    if commander:
        _analysis = analyze_commander(str(commander["oracle_id"]))
        if _analysis:
            _boost_overrides = _analysis.get("boost_overrides", [])

        conf_label = _analysis.get("generation_confidence", "none") if _analysis else "none"
        conf_icon = _GENERATION_CONF_ICONS.get(conf_label, "❓")

        with st.expander(
            f"Commander Analysis: {commander['name']}  {conf_icon} generation confidence: {conf_label}",
            expanded=True,
        ):
            if _analysis is None:
                st.warning("Could not fetch commander analysis (API unavailable).")
            else:
                # Color identity
                colors = " / ".join(_analysis.get("color_identity") or []) or "Colorless"
                st.markdown(f"**Colors:** {colors}")

                # Archetype hint
                hint = _analysis.get("archetype_hint")
                if hint:
                    st.markdown(f"**Inferred deck goal:** {hint}")

                # Signals
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

                # Gaps
                gaps = _analysis.get("gaps", [])
                if gaps:
                    st.markdown("**Gaps** *(mechanics the parser couldn't fully interpret):*")
                    for gap in gaps:
                        st.markdown(f"  ❓ {gap}")
                    st.caption(
                        "These mechanics may reduce generation quality. "
                        "Adding decklists for this commander will improve results."
                    )

                if _boost_overrides:
                    st.caption(f"Score boosts active: {', '.join(_boost_overrides)}")

    # ── Advanced options ───────────────────────────────────────────────────────
    _synergy_alpha = 0.4
    _chosen_checkpoint = "phase4_best"
    if commander:
        with st.expander("Advanced options", expanded=False):
            # Training path selector
            _available = list_checkpoints()
            _path_choice = st.radio(
                "Training path",
                ["Co-occurrence", "Compositional"],
                horizontal=True,
                help=(
                    "Co-occurrence: trained on human deck data.  "
                    "Compositional: trained on oracle-text role reasoning."
                ),
            )
            # Resolve to a concrete checkpoint name
            _default_ckpt = _LATEST_CHECKPOINT.get(_path_choice, "phase4_best")
            # Filter to checkpoints matching the chosen path
            _path_key = "co-occurrence" if _path_choice == "Co-occurrence" else "compositional"
            _matching = [c for c in _available if c["training_path"] == _path_key]
            if _matching:
                _ckpt_options = [c["name"] for c in _matching]
                _default_ckpt_idx = 0
                if _default_ckpt in _ckpt_options:
                    _default_ckpt_idx = _ckpt_options.index(_default_ckpt)
                _chosen_checkpoint = st.selectbox(
                    "Checkpoint",
                    _ckpt_options,
                    index=_default_ckpt_idx,
                    help="Select a specific checkpoint for this training path.",
                )
            else:
                _chosen_checkpoint = _default_ckpt
                st.caption(f"Checkpoint: `{_chosen_checkpoint}` (not yet uploaded)")

            _synergy_alpha = st.slider(
                "Synergy alpha (α)",
                min_value=0.0,
                max_value=1.0,
                value=0.4,
                step=0.05,
                help=(
                    "Blend weight between model score and intra-deck synergy score. "
                    "0.0 = model-only (original behaviour), "
                    "1.0 = synergy-only, "
                    "0.4 = recommended default (40% synergy, 60% model)."
                ),
            )

    # ── Generate button + async polling ───────────────────────────────────────
    if commander and st.button("Generate deck", type="primary"):
        try:
            job_id = submit_deck_job(
                str(commander["oracle_id"]), _chosen_checkpoint, _boost_overrides, _synergy_alpha
            )
            st.session_state["gen_job_id"] = job_id
            st.session_state.pop("last_deck_filename", None)
            st.rerun()
        except httpx.HTTPError as e:
            st.error(f"Failed to submit job: {e}")

    # Poll while a job is in flight
    if "gen_job_id" in st.session_state:
        job_id = st.session_state["gen_job_id"]
        job = None
        try:
            job = poll_job(job_id)
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 404:
                st.error("Generation job was lost (API restarted). Please generate again.")
                del st.session_state["gen_job_id"]
            else:
                st.warning(f"Transient poll error ({e.response.status_code}), retrying…")
                time.sleep(2)
                st.rerun()
        except httpx.HTTPError:
            time.sleep(2)
            st.rerun()

        if job:
            status = job.get("status", "unknown")
            if status in ("queued", "running"):
                st.progress(job.get("progress", 0.0), text=job.get("message", "Working…"))
                time.sleep(1)
                st.rerun()
            elif status == "error":
                st.error(f"Generation failed: {job.get('error', 'unknown error')}")
                del st.session_state["gen_job_id"]
            elif status == "complete":
                filename = job["result"].get("deck_filename")
                st.session_state["last_deck_filename"] = filename
                del st.session_state["gen_job_id"]
                # Bust the list cache so the new deck appears immediately
                list_generated_decks.clear()
                st.rerun()

    # Show completion notice after a successful generation
    if "last_deck_filename" in st.session_state and "gen_job_id" not in st.session_state:
        st.progress(1.0, text="Generation complete!")
        st.info("Your deck is ready. Open the **Generated Decks** tab to view it.")


# ── Generated Decks tab ───────────────────────────────────────────────────────

with tab_history:
    st.subheader("Generated decks")

    decks_list = list_generated_decks()

    if not decks_list:
        st.info("No generated decks yet. Use the Deck Builder tab to generate one.")
    else:
        def _deck_label(d: dict) -> str:
            path = _training_path_from_checkpoint(d.get("checkpoint", ""))
            badge = _PATH_BADGES.get(path, path)
            return f"{d['commander']}  [{badge}]  —  {d['filename']}  ({d['card_count']} cards)"

        options_map = {_deck_label(d): d["filename"] for d in decks_list}
        labels = list(options_map.keys())

        # Auto-select the most recently generated deck if available
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
                    path_a = _training_path_from_checkpoint(deck_a.get("checkpoint", ""))
                    st.markdown(f"### {_PATH_BADGES.get(path_a, path_a)}")
                    render_deck(deck_a)
                with col_b:
                    path_b = _training_path_from_checkpoint(deck_b.get("checkpoint", ""))
                    st.markdown(f"### {_PATH_BADGES.get(path_b, path_b)}")
                    render_deck(deck_b)
        else:
            deck = get_generated_deck(chosen_filename)
            if deck is None:
                st.error("Could not load deck. The file may have been deleted.")
            else:
                render_deck(deck)
