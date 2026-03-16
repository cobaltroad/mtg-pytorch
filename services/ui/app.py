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


@st.cache_data(ttl=300)
def get_similar(oracle_id: str) -> list[dict]:
    r = httpx.get(f"{API_URL}/cards/{oracle_id}/similar", params={"limit": 10}, timeout=10)
    r.raise_for_status()
    return r.json()


def generate_deck(oracle_id: str, checkpoint: str = "latest") -> dict:
    r = httpx.post(
        f"{API_URL}/decks/generate",
        json={"commander_oracle_id": oracle_id, "checkpoint": checkpoint},
        timeout=120,
    )
    r.raise_for_status()
    return r.json()


@st.cache_data(ttl=3600)
def get_metrics() -> dict | None:
    """Fetch Recall@K metrics from the API (cached 1 hour)."""
    try:
        r = httpx.get(f"{API_URL}/decks/metrics", timeout=300)
        r.raise_for_status()
        return r.json()
    except Exception:
        return None


# ── Layout ────────────────────────────────────────────────────────────────────

tab_search, tab_deck, tab_import = st.tabs(["Card Search & Similarity", "Deck Builder", "Import Decklist"])

with tab_search:
    st.subheader("Search cards")
    query = st.text_input("Search by name or rules text", placeholder="draw a card when…")
    if query:
        with st.spinner("Searching…"):
            results = search_cards(query)

        if not results:
            st.info("No results found.")
        else:
            df = pd.DataFrame(results)[["name", "type_line", "mana_cost", "cmc", "oracle_text"]]
            selected = st.dataframe(
                df,
                use_container_width=True,
                on_select="rerun",
                selection_mode="single-row",
                column_config={
                    "oracle_text": st.column_config.TextColumn("oracle_text", width="medium"),
                },
            )

            if selected and selected.selection.rows:
                row = results[selected.selection.rows[0]]
                st.markdown(f"### {row['name']}")
                st.markdown(f"**Type:** {row.get('type_line','—')}  |  **Cost:** {row.get('mana_cost','—')}")
                st.markdown(row.get("oracle_text", ""))

                if st.button("Find similar cards"):
                    with st.spinner("Embedding search…"):
                        similar = get_similar(str(row["oracle_id"]))
                    st.subheader("Similar cards (vector search)")
                    st.dataframe(
                        pd.DataFrame(similar)[["name", "type_line", "mana_cost", "oracle_text"]],
                        use_container_width=True,
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

    checkpoint = st.text_input("Model checkpoint", value="latest")

    if commander and st.button("Generate deck", type="primary"):
        with st.spinner("Generating 99-card deck…"):
            try:
                deck = generate_deck(str(commander["oracle_id"]), checkpoint)
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

                # ── Deck table sorted by score ────────────────────────────────
                rows = [
                    {
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
                        "score": st.column_config.NumberColumn("Score", format="%.4f"),
                    },
                )
            except httpx.HTTPError as e:
                st.error(f"Generation failed: {e}")

with tab_import:
    st.subheader("Import a Moxfield decklist")
    st.markdown(
        "Paste a Moxfield export (or any standard decklist format) below. "
        "Imported decks are used as training signal to improve the model."
    )

    deck_name = st.text_input("Deck name (optional)", placeholder="My Wilhelt build")
    decklist_text = st.text_area(
        "Paste decklist here",
        height=400,
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
        with st.spinner("Parsing and importing…"):
            try:
                r = httpx.post(
                    f"{API_URL}/decks/import",
                    json={"text": decklist_text, "deck_name": deck_name or "Untitled"},
                    timeout=30,
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

            except httpx.HTTPError as e:
                st.error(f"Import failed: {e}")
