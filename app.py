import warnings
# Silence Appwrite Deprecation Warnings for a clean log
warnings.filterwarnings("ignore", category=DeprecationWarning)

import streamlit as st
import gpxpy
import pandas as pd
import plotly.express as px
import re
import bcrypt
import smtplib
from email.message import EmailMessage
import random
import string
import io
from appwrite.client import Client
from appwrite.services.databases import Databases
from appwrite.id import ID
from appwrite.query import Query

# --- Page Configuration ---
st.set_page_config(page_title="Trail Race Planner", layout="wide", initial_sidebar_state="collapsed")

# --- Device Detection ---
def check_if_mobile():
    try:
        user_agent = st.context.headers.get("user-agent", "").lower()
        return any(keyword in user_agent for keyword in ['mobile', 'android', 'iphone', 'ipad'])
    except: return False
is_mobile = check_if_mobile()

# --- Initialize Appwrite ---
try:
    client = Client()
    client.set_endpoint(st.secrets["APPWRITE_ENDPOINT"])
    client.set_project(st.secrets["APPWRITE_PROJECT_ID"])
    client.set_key(st.secrets["APPWRITE_API_KEY"])
    databases = Databases(client)
    
    DB_ID = st.secrets["APPWRITE_DATABASE_ID"]
    U_COL = st.secrets["APPWRITE_USERS_COLLECTION"] # Now treated as Table ID
    R_COL = st.secrets["APPWRITE_RACES_COLLECTION"] # Now treated as Table ID
except Exception as e:
    st.error(f"🚨 Appwrite Connection Error: {e}. Check your secrets!")
    st.stop()

# --- Super-Safe Data Extractor (Updated for Rows/Documents) ---
def get_val(obj, key, default=None):
    if obj is None: return default
    # Relational API returns 'rows', older SDKs return 'documents'
    if key == 'documents':
        try: return obj['rows']
        except: 
            try: return obj['documents']
            except: pass
    
    try: return obj[key]
    except: pass
    try: return getattr(obj, key)
    except: pass
    if key.startswith('$'):
        try: return getattr(obj, key[1:])
        except: pass
        try: return obj[key[1:]]
        except: pass
    return default

# --- Database Query Helper (Migrated to list_rows) ---
def query_user_by_email(email):
    try:
        # Migration: list_documents -> list_rows | collection_id -> table_id
        return databases.list_rows(database_id=DB_ID, table_id=U_COL, queries=[Query.equal("email", email)])
    except Exception as e:
        st.error(f"🚨 Appwrite Database Error: {e}")
        return None

# --- Security & Time Helpers ---
def hash_password(password): 
    return bcrypt.hashpw(password.encode('utf-8'), bcrypt.gensalt()).decode('utf-8')

def verify_password(password, hashed_str):
    if not hashed_str or not isinstance(hashed_str, str): return False
    try: return bcrypt.checkpw(password.encode('utf-8'), hashed_str.encode('utf-8'))
    except ValueError: return False

def pace_to_seconds(p):
    try:
        m, s = map(int, str(p).split(':'))
        return m * 60 + s
    except: return 360

def seconds_to_eta(s_total):
    h, m = divmod(s_total, 3600)
    m, s = divmod(m, 60)
    return f"{h:02d}:{m:02d}:{s:02d}"

def send_reset_email(to_email, temp_password):
    sender = st.secrets.get("SENDER_EMAIL")
    pwd = st.secrets.get("SENDER_APP_PASSWORD")
    if not sender or not pwd: return "SIMULATED"
    
    msg = EmailMessage()
    msg.set_content(f"Your temporary password is: {temp_password}\n\nPlease log in and update your password immediately.")
    msg['Subject'] = 'Password Reset - Trail Race Planner'
    msg['From'], msg['To'] = sender, to_email
    try:
        server = smtplib.SMTP_SSL('smtp.gmail.com', 465)
        server.login(sender, pwd)
        server.send_message(msg)
        server.quit()
        return "SUCCESS"
    except Exception as e: return str(e)

# --- State Management ---
if 'logged_in' not in st.session_state: st.session_state.logged_in = False
if 'email' not in st.session_state: st.session_state.email = ""
if 'guest_mode' not in st.session_state: st.session_state.guest_mode = False

# --- Sidebar Auth ---
st.sidebar.title("Account Access")
if not st.session_state.logged_in:
    c1, c2 = st.sidebar.columns(2)
    with c1: 
        if st.button("👤 Guest", width="stretch"): st.session_state.guest_mode = True
    with c2: 
        if st.button("🔒 Login", width="stretch"): st.session_state.guest_mode = False

    if not st.session_state.guest_mode:
        t = st.sidebar.tabs(["Login", "Sign Up"])
        
        with t[0]:
            em = st.text_input("Email", key="l_em")
            pw = st.text_input("Password", type="password", key="l_pw")
            if st.button("Submit Login", width="stretch"):
                clean_email = em.strip().lower()
                res = query_user_by_email(clean_email)
                if res:
                    total = get_val(res, 'total', 0)
                    docs = get_val(res, 'documents', [])
                    if total > 0:
                        stored_hash = get_val(docs[0], 'password_hash', '')
                        if verify_password(pw, stored_hash):
                            st.session_state.logged_in, st.session_state.email = True, clean_email
                            st.rerun()
                        else: st.error("❌ Incorrect password.")
                    else: st.error("❌ Email not found. Try signing up first.")
            
            with st.expander("Forgot Password?"):
                reset_em = st.text_input("Enter your account email", key="reset_em")
                if st.button("Send Temp Password", width="stretch"):
                    clean_reset = reset_em.strip().lower()
                    res = query_user_by_email(clean_reset)
                    if res and get_val(res, 'total', 0) > 0:
                        temp_pwd = ''.join(random.choices(string.ascii_letters + string.digits, k=12))
                        doc_id = get_val(get_val(res, 'documents', [])[0], '$id')
                        try:
                            # Migration: update_document -> update_row
                            databases.update_row(database_id=DB_ID, table_id=U_COL, row_id=doc_id, data={"password_hash": hash_password(temp_pwd)})
                            email_status = send_reset_email(clean_reset, temp_pwd)
                            if email_status == "SUCCESS": st.success("✅ A temporary password has been sent.")
                            else: st.warning("Simulated password:"); st.code(temp_pwd) 
                        except Exception as e: st.error(f"🚨 Update Error: {e}")
                    else: st.error("❌ Email not found.")
                    
        with t[1]:
            rem = st.text_input("New Email", key="r_em")
            rpw = st.text_input("New Password", type="password", key="r_pw")
            if st.button("Create Account", width="stretch"):
                clean_new = rem.strip().lower()
                if len(rpw) >= 6 and "@" in clean_new:
                    res = query_user_by_email(clean_new)
                    if res is not None and get_val(res, 'total', 0) == 0:
                        try:
                            # Migration: create_document -> create_row
                            databases.create_row(database_id=DB_ID, table_id=U_COL, row_id=ID.unique(), data={"email": clean_new, "password_hash": hash_password(rpw)})
                            st.success("✅ Account created! Please switch to the Login tab.")
                        except Exception as e: st.error(f"🚨 Creation Error: {e}")
                    elif res is not None: st.error("⚠️ Email already exists.")
                else: st.error("Invalid email or password too short.")
else:
    st.sidebar.success(f"User: {st.session_state.email}")
    if st.sidebar.button("Log Out", width="stretch"):
        st.session_state.logged_in = False
        st.rerun()

# --- GPX Processing ---
@st.cache_data
def process_gpx(file_bytes):
    gpx = gpxpy.parse(file_bytes)
    pts = []
    d_acc = 0
    prev = None
    for track in gpx.tracks:
        for segment in track.segments:
            for p in segment.points:
                if prev: d_acc += p.distance_2d(prev)
                pts.append({'dist': d_acc, 'ele': p.elevation})
                prev = p
    df = pd.DataFrame(pts)
    df['km'] = (df['dist'] // 1000).astype(int) + 1
    df['diff'] = df['ele'].diff().fillna(0)
    plan = df.groupby('km').agg(gain=('diff', lambda x: x[x>0].sum()), loss=('diff', lambda x: abs(x[x<0].sum()))).reset_index()
    return plan.round(0).astype(int), df

# --- Main UI ---
st.title("🏔️ Trail Race Planner")
ADMIN_EMAIL = "aihtn2708@gmail.com" 

if st.session_state.logged_in:
    t_names = ["Plan New Race", "My Saved Races", "Account Settings"]
    if st.session_state.email == ADMIN_EMAIL: t_names.append("👑 Admin")
    app_tabs = st.tabs(t_names)
    active_tab, saved_tab, settings_tab = app_tabs[0], app_tabs[1], app_tabs[2]
    admin_tab = app_tabs[3] if len(app_tabs) > 3 else None
else: active_tab = st.container()

with active_tab:
    up = st.file_uploader("Upload GPX File")
    if up:
        if 'p_df' not in st.session_state or st.session_state.get('f_name') != up.name:
            p_df, r_df = process_gpx(up.getvalue())
            p_df['Pace'], p_df['💧'], p_df['🍯'], p_df['🍌'], p_df['🧂'], p_df['Notes'] = "06:00", False, False, False, False, ""
            st.session_state.p_df, st.session_state.r_df, st.session_state.f_name = p_df, r_df, up.name
        
        base_df = st.session_state.p_df.copy()
        base_df['sec'] = base_df['Pace'].apply(pace_to_seconds)
        base_df['ETA'] = base_df['sec'].cumsum().apply(seconds_to_eta)
        display_df = base_df.drop(columns=['sec'])
        
        rdf = st.session_state.r_df
        t_dist, t_gain = rdf['dist'].max()/1000, display_df['gain'].sum()
        
        c1, c2, c3 = st.columns(3)
        c1.metric("Distance", f"{t_dist:.2f} km"); c2.metric("Total Gain", f"{t_gain} m"); c3.metric("Est. Finish", display_df['ETA'].iloc[-1])

        st.plotly_chart(px.area(rdf, x='dist', y='ele', height=250), width="stretch")

        cfg = {
            "km": st.column_config.NumberColumn("KM", width="small", disabled=True),
            "gain": st.column_config.NumberColumn("🔺", width="small", disabled=True, help="Elevation Gain (m)"),
            "loss": st.column_config.NumberColumn("🔻", width="small", disabled=True, help="Elevation Loss (m)"),
            "Pace": st.column_config.TextColumn("Pace", width="small", help="Target Pace (mm:ss)"),
            "ETA": st.column_config.TextColumn("ETA", width="small", disabled=True, help="Estimated Time"),
            "💧": st.column_config.CheckboxColumn("💧", width="small", help="Water"),
            "🍯": st.column_config.CheckboxColumn("🍯", width="small", help="Energy Gel"),
            "🍌": st.column_config.CheckboxColumn("🍌", width="small", help="Real Food"),
            "🧂": st.column_config.CheckboxColumn("🧂", width="small", help="Salt"),
            "Notes": st.column_config.TextColumn("Notes", width="medium")
        }

        if is_mobile:
            with st.form("edit"):
                f_km, t_km = st.columns(2)
                fv = f_km.number_input("From KM", 1, int(display_df['km'].max()), 1)
                tv = t_km.number_input("To KM", 1, int(display_df['km'].max()), int(display_df['km'].max()))
                pv, nutri = st.text_input("New Pace", "06:00"), st.multiselect("Nutrition", ["💧", "🍯", "🍌", "🧂"])
                if st.form_submit_button("Apply", width="stretch"):
                    mask = (st.session_state.p_df['km']>=fv) & (st.session_state.p_df['km']<=tv)
                    st.session_state.p_df.loc[mask, 'Pace'] = pv
                    for i in ["💧", "🍯", "🍌", "🧂"]: st.session_state.p_df.loc[mask, i] = (i in nutri)
                    st.rerun()
            st.dataframe(display_df, hide_index=True, width="stretch", column_config=cfg)
            final_df = display_df
        else:
            final_df = st.data_editor(display_df, hide_index=True, width="stretch", column_config=cfg)
            if not final_df.equals(display_df):
                st.session_state.p_df = final_df.drop(columns=['ETA'])
                st.rerun()
        
        st.download_button("📥 Download CSV", final_df.to_csv(index=False).encode('utf-8-sig'), "plan.csv", "text/csv", width="stretch")
        
        if st.session_state.logged_in:
            rname = st.text_input("Race Name")
            if st.button("💾 Save to Cloud", width="stretch") and rname:
                # Migration: create_document -> create_row
                databases.create_row(database_id=DB_ID, table_id=R_COL, row_id=ID.unique(), data={
                    "email": st.session_state.email, "race_name": rname,
                    "plan_json": final_df.to_json(orient='records'),
                    "distance_km": float(t_dist), "elevation_gain_m": int(t_gain), "finish_time": final_df['ETA'].iloc[-1]
                })
                st.success("Race Saved!")

if st.session_state.logged_in:
    with saved_tab:
        try:
            # Migration: list_documents -> list_rows
            res = databases.list_rows(database_id=DB_ID, table_id=R_COL, queries=[Query.equal("email", st.session_state.email), Query.order_desc("$createdAt")])
            docs = get_val(res, 'documents', [])
        except: docs = []
        for d in docs:
            rid = get_val(d, '$id')
            with st.expander(f"🏁 {get_val(d, 'race_name')} ({get_val(d, 'distance_km'):.1f}km)"):
                st.caption(f"Gain: {get_val(d, 'elevation_gain_m')}m | Time: {get_val(d, 'finish_time')}")
                rdf = pd.read_json(io.StringIO(get_val(d, 'plan_json')))
                if 'sec' in rdf.columns: rdf = rdf.drop(columns=['sec'])
                st.dataframe(rdf, hide_index=True, width="stretch", column_config=cfg)
                c1, c2 = st.columns(2)
                c1.download_button("📥 CSV", rdf.to_csv(index=False).encode('utf-8-sig'), f"{rid}.csv", key=f"dl_{rid}")
                if c2.button("🗑️ Delete", key=f"del_{rid}", width="stretch"):
                    # Migration: delete_document -> delete_row
                    databases.delete_row(database_id=DB_ID, table_id=R_COL, row_id=rid)
                    st.rerun()
