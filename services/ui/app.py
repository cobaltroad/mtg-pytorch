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
        timeout=60,
    )
    r.raise_for_status()
    return r.json()


# ── Layout ────────────────────────────────────────────────────────────────────

tab_search, tab_deck = st.tabs(["Card Search & Similarity", "Deck Builder"])

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
            selected = st.dataframe(df, use_container_width=True, on_select="rerun", selection_mode="single-row")

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
                rows = [
                    {
                        "name": c["name"],
                        "type_line": c.get("type_line", ""),
                        "mana_cost": c.get("mana_cost", ""),
                        "cmc": c.get("cmc", ""),
                        "score": f"{s:.3f}",
                    }
                    for c, s in zip(deck["cards"], deck["scores"])
                ]
                st.dataframe(pd.DataFrame(rows), use_container_width=True, height=600)
            except httpx.HTTPError as e:
                st.error(f"Generation failed: {e}")
