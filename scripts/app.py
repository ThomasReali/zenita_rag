"""Streamlit interface for the Engine SpA Sales Assistant RAG

Run with:  streamlit run scripts/app.py

If streamlit can't find the 'src' package, run from the project root:
    cd /home/thomas/Sync/NextPulse && streamlit run scripts/app.py
"""
from __future__ import annotations

import sys
from pathlib import Path

# Ensure the project root is on sys.path so 'src.nextpulse' import works
_PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

import streamlit as st
from src.nextpulse.rag_chain import RAGChain

st.set_page_config(page_title="Engine SpA — Sales Assistant", layout="centered")

st.title("🚦 Sales Assistant — Traffic Enforcement")
st.markdown(
    "Assistente basato su RAG per supportare il team commerciale di Engine SpA.  \n"
    "Risponde **esclusivamente** in base ai documenti aziendali indicizzati."
)

# ── Session state ────────────────────────────────────────────────────────────

if "rag_chain" not in st.session_state:
    try:
        st.session_state.rag_chain = RAGChain()
    except ValueError as e:
        st.error(
            f"❌ {e}\n\n"
            "Crea un file `.env` con la tua `OPENAI_API_KEY` oppure "
            "imposta la variabile d'ambiente e ricarica la pagina."
        )
        st.stop()

if "messages" not in st.session_state:
    st.session_state.messages = []

# ── Sidebar info ─────────────────────────────────────────────────────────────

with st.sidebar:
    st.markdown("## ℹ️ Info")
    try:
        stats = st.session_state.rag_chain.vector_store.get_stats()
        st.metric("📄 Documenti indicizzati", stats["count"])
    except Exception:
        st.caption("Nessun documento indicizzato.")

    st.markdown("---")
    st.markdown("### 📌 Suggerimenti")
    st.caption(
        "• Fai domande tecniche sui prodotti Engine SpA\n"
        "• L'assistente risponde solo con dati aziendali\n"
        "• Usa la cronologia per domande di follow-up"
    )

# ── Chat history ─────────────────────────────────────────────────────────────

for msg in st.session_state.messages:
    with st.chat_message(msg["role"]):
        st.markdown(msg["content"])

# ── Input ────────────────────────────────────────────────────────────────────

if prompt := st.chat_input("Es: Quali sono le specifiche del T-EXCEED V2.0?"):
    # Add user message
    st.session_state.messages.append({"role": "user", "content": prompt})
    with st.chat_message("user"):
        st.markdown(prompt)

    # Generate response
    with st.chat_message("assistant"):
        with st.spinner("🔍 Ricerca nei documenti Engine SpA..."):
            try:
                # Pass the full chat history (excluding the just-added user msg for reformulation)
                history = st.session_state.messages[:-1]
                result = st.session_state.rag_chain.query(
                    question=prompt,
                    chat_history=history,
                )

                response = result["response"]
                st.markdown(response)

                # Show sources in an expander
                if result.get("sources"):
                    with st.expander("📚 Fonti utilizzate"):
                        for src in result["sources"]:
                            st.caption(f"• {src}")

                st.session_state.messages.append(
                    {"role": "assistant", "content": response}
                )

            except Exception as e:
                error_msg = f"⚠️ Errore: {e}"
                st.error(error_msg)
                st.session_state.messages.append(
                    {"role": "assistant", "content": error_msg}
                )
