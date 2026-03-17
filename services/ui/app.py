"""MTG Commander AI — Streamlit interface."""

import os

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


@st.cache_data(ttl=60)
def list_decks() -> list[dict]:
    r = httpx.get(f"{API_URL}/decks", timeout=15)
    r.raise_for_status()
    return r.json()


def browse_deck(deck_id: str) -> dict:
    r = httpx.get(f"{API_URL}/decks/{deck_id}/browse", timeout=30)
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

tab_browser, tab_deck, tab_import, tab_train = st.tabs(["Decklist Browser", "Deck Builder", "Import Decklist", "Re-Train"])

with tab_browser:
    st.subheader("Human decklist browser")
    st.caption(
        "Browse imported Commander decklists. Opening a deck triggers role-annotation "
        "of each card and writes role_demand synergy edges to the training database."
    )

    # ── Deck list ─────────────────────────────────────────────────────────────
    try:
        all_decks = list_decks()
    except Exception as e:
        st.error(f"Could not load decks: {e}")
        all_decks = []

    if not all_decks:
        st.info("No decks imported yet. Use the **Import Decklist** tab to add decklists.")
    else:
        # Filter by commander name
        commander_filter = st.text_input(
            "Filter by commander", placeholder="Wilhelt, Atraxa…", key="browser_filter"
        )
        filtered = [
            d for d in all_decks
            if not commander_filter
            or commander_filter.lower() in d["commander_name"].lower()
            or commander_filter.lower() in (d.get("deck_name") or "").lower()
        ]

        st.caption(f"{len(filtered)} of {len(all_decks)} decks shown")

        # Build selection table
        deck_rows = [
            {
                "deck_name":      d.get("deck_name") or "—",
                "commander":      d["commander_name"],
                "colors":         " ".join(d.get("commander_colors") or []),
                "source":         d["source"],
                "cards":          d["card_count"],
                "_deck_id":       d["deck_id"],
            }
            for d in filtered
        ]
        deck_df = pd.DataFrame(deck_rows)

        selected_deck = st.dataframe(
            deck_df[["deck_name", "commander", "colors", "source", "cards"]],
            use_container_width=True,
            on_select="rerun",
            selection_mode="single-row",
        )

        # ── Selected deck detail ───────────────────────────────────────────────
        if selected_deck and selected_deck.selection.rows:
            chosen = deck_rows[selected_deck.selection.rows[0]]
            st.divider()
            st.markdown(f"### {chosen['deck_name'] or chosen['commander']}  —  {chosen['commander']}")

            with st.spinner("Loading deck and annotating roles…"):
                try:
                    deck_detail = browse_deck(chosen["_deck_id"])
                except Exception as e:
                    st.error(f"Could not load deck: {e}")
                    deck_detail = None

            if deck_detail:
                # Archetypes
                archetypes = deck_detail.get("archetypes", [])
                if archetypes:
                    st.markdown("**Archetypes:** " + "  `" + "`  `".join(archetypes) + "`")

                # Role counts summary
                role_counts = deck_detail.get("role_counts", {})
                if role_counts:
                    rc_cols = st.columns(len(role_counts))
                    for col, (role, cnt) in zip(rc_cols, sorted(role_counts.items())):
                        col.metric(role, cnt)

                # Cards table
                cards = deck_detail.get("cards", [])
                card_rows = [
                    {
                        "name":      c["name"],
                        "type":      c.get("type_line", ""),
                        "cost":      c.get("mana_cost", ""),
                        "cmc":       c.get("cmc", ""),
                        "roles":     " | ".join(
                            r["role"] for r in c.get("roles", [])
                        ) or "—",
                        "effects":   " | ".join(
                            r["effect_class"] for r in c.get("roles", [])
                        ) or "—",
                    }
                    for c in cards
                ]
                if card_rows:
                    st.dataframe(
                        pd.DataFrame(card_rows),
                        use_container_width=True,
                        height=600,
                        column_config={
                            "roles":   st.column_config.TextColumn("roles", width="medium"),
                            "effects": st.column_config.TextColumn("effect tags", width="medium"),
                        },
                    )

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
                if context_cards:
                    with st.expander(f"Context seed ({len(context_cards)} archetype staples used to prime the decoder)"):
                        st.write(", ".join(context_cards))
                else:
                    st.warning(
                        f"No training decks found for **{deck['commander']['name']}**. "
                        "The model is flying blind — results will be poor. "
                        "Add decklists in the **Import Decklist** tab to improve accuracy."
                    )

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
                        "score": st.column_config.NumberColumn("Score", format="%.4f"),
                    },
                )
            except httpx.HTTPError as e:
                st.error(f"Generation failed: {e}")

with tab_import:
    st.subheader("Import a Moxfield decklist")
    st.markdown(
        "Paste a Moxfield export (or any standard decklist format) below. "
        "After import the model does a first-pass analysis — vote on its "
        "interpretation to refine training signal."
    )

    deck_name = st.text_input("Deck name (optional)", placeholder="My Wilhelt build")
    decklist_text = st.text_area(
        "Paste decklist here",
        height=300,
        placeholder=(
            "Commander\n"
            "1 Wilhelt, the Rotcleaver\n\n"
            "Mainboard\n"
            "1 Sol Ring\n"
            "1 Arcane Signet\n"
            "..."
        ),
    )

    if st.button("Import", type="primary") and decklist_text.strip():
        with st.spinner("Parsing, importing, and running first-pass analysis…"):
            try:
                r = httpx.post(
                    f"{API_URL}/decks/import",
                    json={"text": decklist_text, "deck_name": deck_name or "Untitled"},
                    timeout=60,
                )
                r.raise_for_status()
                result = r.json()

                if not result["ok"]:
                    st.error(result["message"])
                elif result["duplicate"]:
                    st.info(f"Already in database: {result['message']}")
                else:
                    st.success(result["message"])

                if result.get("commander"):
                    st.markdown(f"**Commander:** {result['commander']}")
                if result.get("unresolved"):
                    with st.expander(f"{len(result['unresolved'])} unresolved card(s) — click to see"):
                        st.markdown(
                            "These names weren't found in the database. "
                            "They may be universe-beyond alternate names — "
                            "add them to `card_name_aliases.csv` to fix."
                        )
                        for name in result["unresolved"]:
                            st.text(f"  • {name}")

                # ── First-pass analysis panel ─────────────────────────────────
                analysis = result.get("analysis")
                deck_id  = result.get("deck_id")
                if analysis and not result.get("duplicate"):
                    st.divider()
                    st.markdown("### First-pass model analysis")

                    # Archetypes with vote buttons
                    archetypes = analysis.get("archetypes", [])
                    role_counts = analysis.get("role_counts", {})

                    if archetypes:
                        st.markdown("**Detected archetypes** — vote to confirm or reject:")
                        arch_vote_state = {}
                        arch_cols = st.columns(min(len(archetypes), 4))
                        for i, arch in enumerate(archetypes):
                            with arch_cols[i % len(arch_cols)]:
                                st.caption(arch)
                                pkey = f"vote_arch_up_{arch}"
                                dkey = f"vote_arch_dn_{arch}"
                                up = st.button("+1", key=pkey)
                                dn = st.button("-1", key=dkey)
                                if up:
                                    arch_vote_state[arch] = 1
                                elif dn:
                                    arch_vote_state[arch] = -1

                    if role_counts:
                        st.markdown("**Role distribution** (from first-pass):")
                        rc_cols = st.columns(len(role_counts))
                        for col, (role, cnt) in zip(rc_cols, sorted(role_counts.items())):
                            col.metric(role, cnt)

                    # Per-card role table with inline voting
                    cards = analysis.get("cards", [])
                    if cards:
                        st.markdown("**Card role assignments** — click to vote:")
                        vote_payloads: list[dict] = []

                        card_rows = [
                            {
                                "name":    c["name"],
                                "type":    c.get("type_line", ""),
                                "cost":    c.get("mana_cost", ""),
                                "roles":   " | ".join(r["role"] for r in c.get("roles", [])) or "—",
                                "effects": " | ".join(r["effect_class"] for r in c.get("roles", [])) or "—",
                                "_card_id": c["card_id"],
                                "_roles":   [r["role"] for r in c.get("roles", [])],
                            }
                            for c in cards
                        ]
                        st.dataframe(
                            pd.DataFrame(card_rows)[["name", "type", "cost", "roles", "effects"]],
                            use_container_width=True,
                            height=400,
                        )

                    # Submit votes button
                    if deck_id and st.button("Submit votes & amend", type="secondary"):
                        votes_to_send = []
                        for arch, v in arch_vote_state.items():
                            votes_to_send.append({"archetype": arch, "vote": v})
                        if votes_to_send:
                            with st.spinner("Applying votes and amending…"):
                                try:
                                    httpx.post(
                                        f"{API_URL}/decks/{deck_id}/vote",
                                        json={"votes": votes_to_send},
                                        timeout=15,
                                    ).raise_for_status()
                                    httpx.post(
                                        f"{API_URL}/decks/{deck_id}/amend",
                                        timeout=30,
                                    ).raise_for_status()
                                    st.success("Votes applied. Training edges updated.")
                                except httpx.HTTPError as e:
                                    st.error(f"Vote submission failed: {e}")
                        else:
                            st.info("No votes to submit.")

            except httpx.HTTPError as e:
                st.error(f"Import failed: {e}")

with tab_train:
    st.subheader("Re-Train")
    st.markdown(
        "Launch training runs against the current deck database. "
        "Phase 3 first, then Phase 4 once Phase 3 stabilises."
    )

    # ── Phase 2 ───────────────────────────────────────────────────────────────
    st.markdown("### Phase 2 — Ability-trigger synergy")
    p2col1, p2col2, p2col3 = st.columns(3)
    p2_epochs           = p2col1.number_input("Epochs", value=20, min_value=1, max_value=200, key="p2_epochs")
    p2_lr               = p2col2.number_input("Learning rate", value=1e-4, format="%.0e", key="p2_lr")
    p2_resume           = p2col3.checkbox("Resume from phase2_best", value=True, key="p2_resume")
    p2col4, p2col5      = st.columns(2)
    p2_sample           = p2col4.number_input("ability_trigger sample", value=500_000, min_value=10_000, step=50_000, key="p2_sample")
    p2_role_demand      = p2col5.number_input("role_demand sample (0=off)", value=100_000, min_value=0, step=10_000, key="p2_role_demand")

    if st.button("Start Phase 2", type="primary", key="start_p2"):
        with st.spinner("Launching Phase 2 trainer…"):
            try:
                r = httpx.post(f"{API_URL}/train/start", json={
                    "phase": 2, "epochs": int(p2_epochs), "lr": p2_lr, "resume": p2_resume,
                    "sample": int(p2_sample), "role_demand_sample": int(p2_role_demand),
                }, timeout=15)
                r.raise_for_status()
                info = r.json()
                st.success(f"Phase 2 started — container `{info['short_id']}` ({info['name']})")
                st.caption(f"Command: `{info['command']}`")
            except httpx.HTTPError as e:
                st.error(f"Failed to start Phase 2: {e}")

    st.divider()

    # ── Phase 3 ───────────────────────────────────────────────────────────────
    st.markdown("### Phase 3 — Deck co-occurrence (BPR)")
    p3col1, p3col2, p3col3 = st.columns(3)
    p3_epochs = p3col1.number_input("Epochs", value=50, min_value=1, max_value=200, key="p3_epochs")
    p3_lr     = p3col2.number_input("Learning rate", value=1e-4, format="%.0e", key="p3_lr")
    p3_resume = p3col3.checkbox("Resume from phase3_best", value=True, key="p3_resume")

    if st.button("Start Phase 3", type="primary", key="start_p3"):
        with st.spinner("Launching Phase 3 trainer…"):
            try:
                r = httpx.post(f"{API_URL}/train/start", json={
                    "phase": 3, "epochs": int(p3_epochs), "lr": p3_lr, "resume": p3_resume,
                }, timeout=15)
                r.raise_for_status()
                info = r.json()
                st.success(f"Phase 3 started — container `{info['short_id']}` ({info['name']})")
                st.caption(f"Command: `{info['command']}`")
            except httpx.HTTPError as e:
                st.error(f"Failed to start Phase 3: {e}")

    st.divider()

    # ── Phase 4 ───────────────────────────────────────────────────────────────
    st.markdown("### Phase 4 — Deck construction (InfoNCE)")
    p4col1, p4col2 = st.columns(2)
    p4_epochs       = p4col1.number_input("Epochs", value=50, min_value=1, max_value=200, key="p4_epochs")
    p4_lr           = p4col2.number_input("Learning rate", value=1e-4, format="%.0e", key="p4_lr")
    p4col3, p4col4  = st.columns(2)
    p4_freeze       = p4col3.checkbox("Freeze encoder", value=False, key="p4_freeze")
    p4_enc_scale    = p4col4.number_input("Encoder LR scale", value=0.1, min_value=0.01, max_value=1.0, key="p4_enc_scale", disabled=p4_freeze)
    p4col5, p4col6, p4col7 = st.columns(3)
    p4_resume       = p4col5.checkbox("Resume from phase4_best", value=False, key="p4_resume")
    p4_temp_start   = p4col6.number_input("Temp start", value=0.5, min_value=0.05, max_value=2.0, key="p4_ts")
    p4_temp_end     = p4col7.number_input("Temp end", value=0.05, min_value=0.01, max_value=1.0, key="p4_te")

    if st.button("Start Phase 4", type="primary", key="start_p4"):
        with st.spinner("Launching Phase 4 trainer…"):
            try:
                r = httpx.post(f"{API_URL}/train/start", json={
                    "phase": 4, "epochs": int(p4_epochs), "lr": p4_lr, "resume": p4_resume,
                    "freeze_encoder": p4_freeze, "encoder_lr_scale": p4_enc_scale,
                    "temp_start": p4_temp_start, "temp_end": p4_temp_end,
                }, timeout=15)
                r.raise_for_status()
                info = r.json()
                st.success(f"Phase 4 started — container `{info['short_id']}` ({info['name']})")
                st.caption(f"Command: `{info['command']}`")
            except httpx.HTTPError as e:
                st.error(f"Failed to start Phase 4: {e}")

    st.divider()

    # ── Active / recent runs ───────────────────────────────────────────────────
    st.markdown("### Recent runs")
    if st.button("Refresh", key="refresh_runs"):
        st.rerun()

    try:
        runs_r = httpx.get(f"{API_URL}/train/runs", timeout=10)
        runs_r.raise_for_status()
        runs = runs_r.json()
    except Exception:
        runs = []

    if not runs or (len(runs) == 1 and "error" in runs[0]):
        st.info("No training runs found (or Docker socket unavailable).")
    else:
        for run in runs:
            if "error" in run:
                st.error(run["error"])
                continue
            status_icon = "🟢" if run["status"] == "running" else ("✅" if run.get("exit_code") == 0 else "⬜")
            with st.expander(f"{status_icon} Phase {run['phase']} — `{run['short_id']}` — {run['status']}"):
                st.caption(f"Started: {run.get('started', '—')}  |  Finished: {run.get('finished', '—')}")
                if run["status"] == "running":
                    if st.button("Stop", key=f"stop_{run['container_id']}"):
                        try:
                            httpx.post(f"{API_URL}/train/stop/{run['container_id']}", timeout=15).raise_for_status()
                            st.warning("Stop signal sent.")
                        except httpx.HTTPError as e:
                            st.error(f"Stop failed: {e}")
                try:
                    logs_r = httpx.get(f"{API_URL}/train/logs/{run['container_id']}", params={"tail": 50}, timeout=10)
                    logs_r.raise_for_status()
                    st.code(logs_r.json()["logs"], language=None)
                except Exception:
                    st.caption("(logs unavailable)")
