"""MTG Commander AI — Streamlit interface."""

import os

import httpx
import pandas as pd
import streamlit as st

API_URL          = os.environ.get("API_URL", "http://api:8000")
EXTERNAL_API_URL = os.environ.get("EXTERNAL_API_URL", API_URL)

st.set_page_config(page_title="MTG Commander AI", page_icon="🃏", layout="wide")
st.title("🃏 MTG Commander AI")


# ── Helpers ───────────────────────────────────────────────────────────────────

@st.cache_data(ttl=300)
def search_cards(q: str) -> list[dict]:
    r = httpx.get(f"{API_URL}/cards/search", params={"q": q, "limit": 20}, timeout=10)
    r.raise_for_status()
    return r.json()


def generate_deck(oracle_id: str, checkpoint: str = "latest", boost_overrides: list[str] | None = None) -> dict:
    r = httpx.post(
        f"{API_URL}/decks/generate",
        json={
            "commander_oracle_id": oracle_id,
            "checkpoint": checkpoint,
            "boost_overrides": boost_overrides or [],
        },
        timeout=120,
    )
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


@st.cache_data(ttl=3600)
def get_metrics() -> dict | None:
    """Fetch Recall@K metrics from the API (cached 1 hour)."""
    try:
        r = httpx.get(f"{API_URL}/decks/metrics", timeout=300)
        r.raise_for_status()
        return r.json()
    except Exception:
        return None


# ── UI constants ──────────────────────────────────────────────────────────────

_CONFIDENCE_ICONS = {"high": "✅", "medium": "⚠️", "low": "❓", "unknown": "❓"}
_GENERATION_CONF_ICONS = {"high": "🟢", "medium": "🟡", "low": "🟠", "none": "🔴"}


# ── Layout ────────────────────────────────────────────────────────────────────

tab_deck, tab_dataset, tab_train = st.tabs(["Deck Builder", "Training Data", "Upload Model"])

with tab_deck:
    st.subheader("Commander deck builder")
    st.markdown(
        "Search for a commander, then let the model construct the remaining 99 cards."
    )

    # ── Model performance metrics panel ───────────────────────────────────────
    metrics = get_metrics()
    if metrics and "error" not in metrics:
        st.markdown("#### Model Performance (phase4_best)")
        col1, col2, col3, col4, col5 = st.columns(5)
        col1.metric("Recall@1", f"{metrics.get('recall_1', 0):.1%}")
        col2.metric("Recall@10", f"{metrics.get('recall_10', 0):.1%}")
        col3.metric("Recall@50", f"{metrics.get('recall_50', 0):.1%}")
        col4.metric("MRR", f"{metrics.get('mrr', 0):.4f}")
        col5.metric("Random baseline", f"{metrics.get('random_baseline', 0):.1%}")
        st.caption(
            f"Evaluated on {metrics.get('n_positions', '?')} positions from held-out decks."
        )
    elif metrics and "error" in metrics:
        st.info(f"Model metrics unavailable: {metrics['error']}")
    else:
        st.info("Model metrics not yet available (API may still be loading embeddings).")

    st.divider()

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

    checkpoint = st.text_input("Model checkpoint", value="latest")

    if commander and st.button("Generate deck", type="primary"):
        with st.spinner("Generating 99-card deck…"):
            try:
                deck = generate_deck(str(commander["oracle_id"]), checkpoint, _boost_overrides)
                st.success(f"Deck generated with checkpoint `{deck['checkpoint']}`")
                st.markdown(f"**Commander:** {deck['commander']['name']}")

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
                col1, col2, col3 = st.columns(3)
                col1.metric("Lands", f"{land_count} / {total_count + 1}")  # +1 for commander
                col2.metric("Ramp", str(ramp_count))
                col3.metric("Non-land spells", f"{total_count - land_count} / {total_count + 1}")

                # ── Mana curve bar chart ──────────────────────────────────────
                spells = [
                    c for c in deck["cards"]
                    if "Land" not in c.get("type_line", "")
                ]
                curve: dict[str, int] = {}
                for c in spells:
                    cmc = c.get("cmc") or 0
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
                        "score": st.column_config.NumberColumn("Score", format="%.4f"),
                    },
                )
            except httpx.HTTPError as e:
                st.error(f"Generation failed: {e}")

with tab_dataset:
    st.subheader("Training Data Artifact")
    st.markdown(
        "After the full ingest pipeline completes (including `export_dataset`), "
        "a self-contained `.pt` artifact is available here.  "
        "Download it to the GPU machine and point the trainer at it — "
        "no database connection required."
    )

    try:
        info_r = httpx.get(f"{API_URL}/dataset/info", timeout=10)
        if info_r.status_code == 404:
            st.warning(
                "No artifact found. Run the full ingest pipeline to generate one:\n\n"
                "```\ndocker compose run --rm ingest\n```"
            )
        else:
            info_r.raise_for_status()
            info = info_r.json()

            size_mb = info.get("size_bytes", 0) / 1e6
            created = info.get("created_at", "unknown")[:19].replace("T", " ")

            col1, col2, col3, col4 = st.columns(4)
            col1.metric("Cards", f"{info.get('card_count', 0):,}")
            col2.metric("Training pairs", f"{info.get('synergy_count', 0):,}")
            col3.metric("Decks", f"{info.get('deck_count', 0):,}")
            col4.metric("Phase 4 positions", f"{info.get('position_count', 0):,}")

            st.caption(
                f"Model: `{info.get('model', '?')}`  |  "
                f"Dim: {info.get('dim', '?')}  |  "
                f"Size: {size_mb:.0f} MB  |  "
                f"Created: {created} UTC"
            )

            download_url = f"{EXTERNAL_API_URL}/dataset/download"
            st.markdown(f"**Download URL:** `{download_url}`")
            st.markdown(
                "Use this URL with `download_dataset.ps1` on the GPU machine:\n\n"
                "```powershell\n"
                ".\\scripts\\download_dataset.ps1\n"
                "```\n\n"
                "Or download directly from your browser:"
            )
            st.link_button("Download mtg_dataset.pt", download_url, type="primary")

    except httpx.HTTPError as e:
        st.error(f"Could not reach API: {e}")

with tab_train:
    st.subheader("Upload Phase 4 Checkpoint")
    st.markdown(
        "Upload a `phase4_best.pt` (or any phase checkpoint) trained on the GPU machine. "
        "The model cache is cleared immediately so the next deck generation uses the new weights."
    )

    ck_col1, ck_col2 = st.columns([2, 1])
    ck_name  = ck_col1.selectbox("Save as", ["phase4_best", "phase3_best", "phase2_best", "phase1_best"], index=0)
    ck_token = ck_col2.text_input("Admin token", type="password")
    ck_file  = st.file_uploader("Checkpoint file (.pt)", type=["pt"])

    if st.button("Upload", type="primary", disabled=ck_file is None):
        with st.spinner("Uploading…"):
            try:
                r = httpx.post(
                    f"{API_URL}/admin/checkpoint",
                    files={"file": (ck_file.name, ck_file.getvalue(), "application/octet-stream")},
                    params={"name": ck_name},
                    headers={"x-admin-token": ck_token},
                    timeout=60,
                )
                r.raise_for_status()
                info = r.json()
                st.success(
                    f"Uploaded **{info['bytes']:,} bytes** → `{info['saved']}`. "
                    "Model cache cleared — next generation will use the new checkpoint."
                )
            except httpx.HTTPStatusError as e:
                st.error(f"Upload failed ({e.response.status_code}): {e.response.text}")
            except httpx.HTTPError as e:
                st.error(f"Upload failed: {e}")
