import streamlit as st
import pandas as pd
import sqlite3
import io
import re
import torch
from sentence_transformers import SentenceTransformer, util

st.set_page_config(page_title="SKU ML Batch Mapper (With History)", layout="wide")

# ==========================================
# 1. ML Model Initialization
# ==========================================
@st.cache_resource
def load_model():
    return SentenceTransformer('all-MiniLM-L6-v2')

model = load_model()

# ==========================================
# 2. Text Normalization 
# ==========================================
ABBREV = {
    "al": "alpenliebe", "alp": "alpenliebe", "cc": "chupa chups", "mt": "mentos",
    "mts": "mentos", "gl": "golia", "bb": "big babol", "llp": "lollipop",
    "straw": "strawberry", "pfruit": "passionfruit", "chiaki": "chia kiwi",
    "px": "pouch", "pcs": "pieces", "pc": "pieces"
}

def clean_text(text):
    if not isinstance(text, str): return ""
    text = text.lower()
    text = re.sub(r'[()/.,&]', ' ', text)
    text = re.sub(r'\d+(\.\d+)?\s*(g|kg|px|pcs|pc|box|bag|bags|stick|sticks|tin)\b', ' ', text)
    return " ".join([ABBREV.get(w, w) for w in text.split()])

# ==========================================
# 3. Application UI
# ==========================================
st.title("🧠 Advanced SKU ML Mapper (History-Aware)")
st.markdown("This version learns from your historical data. It auto-carries exact matches and uses historical 'messy' descriptions to improve ML fuzzy matching for unseen items.")

col1, col2, col3 = st.columns(3)

with col1:
    st.subheader("1. Mother Registry")
    st.caption("Canonical list of products")
    mother_file = st.file_uploader("Upload Mother SKUs", type=['xlsx', 'csv'], key="mother")

with col2:
    st.subheader("2. Historical Data")
    st.caption("Previous Child -> Mother mappings")
    hist_file = st.file_uploader("Upload History", type=['xlsx', 'csv'], key="history")

with col3:
    st.subheader("3. New Child SKUs")
    st.caption("Unmapped raw data")
    child_file = st.file_uploader("Upload New SKUs", type=['xlsx', 'csv'], key="child")


if mother_file and hist_file and child_file:
    if st.button("🚀 Run Advanced ML Pipeline", type="primary"):
        with st.spinner("Analyzing history and running ML embeddings..."):
            
            # --- Step A: Read Files ---
            read_file = lambda f: pd.read_excel(f) if f.name.endswith('.xlsx') else pd.read_csv(f)
            df_mother = read_file(mother_file)
            df_hist = read_file(hist_file)
            df_child = read_file(child_file)
            
            # Helper to find columns dynamically
            def get_col(df, keywords):
                return next((c for c in df.columns if any(k in str(c).lower() for k in keywords)), df.columns[0])

            m_code = get_col(df_mother, ['code', 'id'])
            m_desc = get_col(df_mother, ['desc', 'name'])
            
            h_c_code = get_col(df_hist, ['child', 'code'])
            h_c_desc = get_col(df_hist, ['child', 'desc'])
            h_m_code = get_col(df_hist, ['mother', 'target'])
            
            c_code = get_col(df_child, ['code', 'id'])
            c_desc = get_col(df_child, ['desc', 'name'])

            # --- Step B: Build Exact History Dictionary ---
            # Creates a fast lookup mapping known child codes to their mother codes
            history_dict = dict(zip(df_hist[h_c_code].astype(str), df_hist[h_m_code].astype(str)))

            # --- Step C: ML Embeddings ---
            st.toast("Generating embeddings...", icon="🧠")
            
            # Clean texts
            mother_clean = df_mother[m_desc].apply(clean_text).tolist()
            hist_clean = df_hist[h_c_desc].apply(clean_text).tolist()
            child_clean = df_child[c_desc].apply(clean_text).tolist()
            
            # Encode texts into ML vectors
            mother_embs = model.encode(mother_clean, convert_to_tensor=True)
            hist_embs = model.encode(hist_clean, convert_to_tensor=True)
            child_embs = model.encode(child_clean, convert_to_tensor=True)
            
            # --- Step D: The Triage Matching Engine ---
            st.toast("Triaging SKUs...", icon="🔍")
            
            results = []
            
            for i, row in df_child.iterrows():
                current_code = str(row[c_code])
                current_emb = child_embs[i]
                
                # TIER 1: Exact History Match
                if current_code in history_dict:
                    results.append({
                        "child_code": current_code,
                        "predicted_mother_code": history_dict[current_code],
                        "confidence_score": 100.0,
                        "match_method": "Auto-carry (History)"
                    })
                    continue
                    
                # TIER 2 & 3: Semantic ML Match
                # Compare against canonical Mothers
                sim_mother = util.cos_sim(current_emb, mother_embs)[0]
                best_m_score, best_m_idx = torch.max(sim_mother, dim=0)
                
                # Compare against messy Historical Children
                sim_hist = util.cos_sim(current_emb, hist_embs)[0]
                best_h_score, best_h_idx = torch.max(sim_hist, dim=0)
                
                # Choose the path with higher confidence
                if best_h_score > best_m_score:
                    # The new messy SKU looks very much like an old messy SKU
                    best_mother = df_hist.iloc[best_h_idx.item()][h_m_code]
                    score = best_h_score.item() * 100
                    method = "ML Fuzzy (Matched via History)"
                else:
                    # The new messy SKU looks more like a clean Mother SKU
                    best_mother = df_mother.iloc[best_m_idx.item()][m_code]
                    score = best_m_score.item() * 100
                    method = "ML Fuzzy (Matched direct to Mother)"
                    
                results.append({
                    "child_code": current_code,
                    "predicted_mother_code": best_mother,
                    "confidence_score": round(score, 1),
                    "match_method": method
                })

            df_results = pd.DataFrame(results)
            df_child = df_child.merge(df_results, left_on=c_code, right_on="child_code", how="left")
            
            # --- Step E: In-Memory SQLite Join ---
            conn = sqlite3.connect(':memory:')
            df_mother.to_sql('mothers', conn, index=False)
            df_child.to_sql('children', conn, index=False)
            
            query = f"""
                SELECT 
                    c.*,
                    m."{m_desc}" as predicted_mother_desc
                FROM children c
                LEFT JOIN mothers m 
                ON c.predicted_mother_code = m."{m_code}"
                ORDER BY c.confidence_score DESC
            """
            
            final_df = pd.read_sql_query(query, conn)
            conn.close()
            
            # --- Step F: Display & Export ---
            st.success("Mapping Complete!")
            
            st.dataframe(
                final_df[[c_code, c_desc, 'predicted_mother_code', 'predicted_mother_desc', 'confidence_score', 'match_method']].head(50), 
                use_container_width=True,
                column_config={"confidence_score": st.column_config.ProgressColumn("Confidence (%)", min_value=0, max_value=100)}
            )
            
            buffer = io.BytesIO()
            with pd.ExcelWriter(buffer, engine='openpyxl') as writer:
                final_df.to_excel(writer, index=False, sheet_name='Mapped_SKUs')
            
            st.download_button(
                label="⬇️ Download Full Mapped Results",
                data=buffer.getvalue(),
                file_name="History_Aware_SKU_Mappings.xlsx",
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                type="primary"
            )
