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
    U_COL = st.secrets["APPWRITE_USERS_COLLECTION"]
    R_COL = st.secrets["APPWRITE_RACES_COLLECTION"]
except Exception as e:
    st.error(f"Appwrite Connection Error: {e}. Check your secrets!")
    st.stop()

# --- Security & Time Helpers ---
def hash_password(password): return bcrypt.hashpw(password.encode('utf-8'), bcrypt.gensalt()).decode('utf-8')
def verify_password(password, hashed): return bcrypt.checkpw(password.encode('utf-8'), hashed.encode('utf-8'))
def pace_to_seconds(p):
    try:
        m, s = map(int, str(p).split(':'))
        return m * 60 + s
    except: return 360
def seconds_to_eta(s_total):
    h, m = divmod(s_total, 3600)
    m, s = divmod(m, 60)
    return f"{h:02d}:{m:02d}:{s:02d}"

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
                res = databases.list_documents(DB_ID, U_COL, [Query.equal("email", em)])
                if res['total'] > 0 and verify_password(pw, res['documents'][0]['password_hash']):
                    st.session_state.logged_in, st.session_state.email = True, em
                    st.rerun()
                else: st.error("Invalid email or password.")
        with t[1]:
            rem = st.text_input("New Email", key="r_em")
            rpw = st.text_input("New Password", type="password", key="r_pw")
            if st.button("Create Account", width="stretch"):
                if len(rpw) >= 6:
                    exists = databases.list_documents(DB_ID, U_COL, [Query.equal("email", rem)])
                    if exists['total'] == 0:
                        databases.create_document(DB_ID, U_COL, ID.unique(), {"email": rem, "password_hash": hash_password(rpw)})
                        st.success("Account created! Please log in.")
                    else: st.error("An account with this email already exists.")
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
    for t in gpx.tracks:
        for s in t.segments:
            for p in s.points:
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
admin_mail = "aihtn2708@gmail.com"

if st.session_state.logged_in:
    t_names = ["Plan New Race", "My Saved Races", "Account Settings"]
    if st.session_state.email == admin_mail: t_names.append("👑 Admin")
    app_tabs = st.tabs(t_names)
    active_tab, saved_tab, settings_tab = app_tabs[0], app_tabs[1], app_tabs[2]
    admin_tab = app_tabs[3] if len(app_tabs) > 3 else None
else:
    active_tab = st.container()

with active_tab:
    up = st.file_uploader("Upload GPX File")
    if up:
        if 'p_df' not in st.session_state or st.session_state.get('f_name') != up.name:
            p_df, r_df = process_gpx(up.getvalue())
            
            # Setup initial state
            p_df['Pace'] = "06:00"
            p_df['💧'] = False
            p_df['🍯'] = False
            p_df['🍌'] = False
            p_df['🧂'] = False
            p_df['Notes'] = ""
            st.session_state.p_df, st.session_state.r_df, st.session_state.f_name = p_df, r_df, up.name
        
        # 1. Grab base data and dynamically calculate ETA FIRST
        base_df = st.session_state.p_df.copy()
        base_df['sec'] = base_df['Pace'].apply(pace_to_seconds)
        base_df['ETA'] = base_df['sec'].cumsum().apply(seconds_to_eta)
        display_df = base_df.drop(columns=['sec']) # Hide math column
        
        rdf = st.session_state.r_df
        t_dist = rdf['dist'].max() / 1000
        t_gain = display_df['gain'].sum()
        total_time = display_df['ETA'].iloc[-1]
        
        # 2. Render Header Metrics
        col1, col2, col3 = st.columns(3)
        col1.metric("Distance", f"{t_dist:.2f} km")
        col2.metric("Total Gain", f"{t_gain} m")
        col3.metric("Estimated Finish", total_time)

        st.plotly_chart(px.area(rdf, x='dist', y='ele', height=250), width="stretch")

        # 3. Strict Column Config (with tooltips added via 'help')
        cfg = {
            "km": st.column_config.NumberColumn("KM", width="small", disabled=True),
            "gain": st.column_config.NumberColumn("🔺", width="small", disabled=True, help="Elevation Gain (m)"),
            "loss": st.column_config.NumberColumn("🔻", width="small", disabled=True, help="Elevation Loss (m)"),
            "Pace": st.column_config.TextColumn("Pace", width="small", help="Target Pace (mm:ss)"),
            "ETA": st.column_config.TextColumn("ETA", width="small", disabled=True, help="Estimated Time of Arrival"),
            "💧": st.column_config.CheckboxColumn("💧", width="small", help="Water"),
            "🍯": st.column_config.CheckboxColumn("🍯", width="small", help="Energy Gel"),
            "🍌": st.column_config.CheckboxColumn("🍌", width="small", help="Real Food / Solid Nutrition"),
            "🧂": st.column_config.CheckboxColumn("🧂", width="small", help="Salt / Electrolyte Pills"),
            "Notes": st.column_config.TextColumn("Notes", width="medium", help="Optional strategy notes")
        }

        # 4. Render ONE Unified Table
        if is_mobile:
            st.info("📱 **Mobile View:** Swipe left/right on the table below to view all columns.")
            with st.form("mobile_edit"):
                f_km, t_km = st.columns(2)
                f_v = f_km.number_input("From KM", 1, int(display_df['km'].max()), 1)
                t_v = t_km.number_input("To KM", 1, int(display_df['km'].max()), int(display_df['km'].max()))
                p_v = st.text_input("New Pace (mm:ss)", "06:00")
                nutri = st.multiselect("Nutrition", ["💧", "🍯", "🍌", "🧂"], help="Select what to consume in this section")
                
                if st.form_submit_button("Apply Changes", width="stretch"):
                    # Update state and refresh
                    mask = (st.session_state.p_df['km'] >= f_v) & (st.session_state.p_df['km'] <= t_v)
                    st.session_state.p_df.loc[mask, 'Pace'] = p_v
                    for icon in ["💧", "🍯", "🍌", "🧂"]:
                        st.session_state.p_df.loc[mask, icon] = (icon in nutri)
                    st.rerun()
            
            # Static view for mobile swiping
            st.dataframe(display_df, hide_index=True, width="stretch", column_config=cfg)
            final_display_df = display_df
        else:
            st.info("💻 **Desktop View:** Click directly into the table cells below to edit your pace and nutrition.")
            
            # Single interactive editor including the locked ETA column
            edit_df = st.data_editor(display_df, hide_index=True, width="stretch", column_config=cfg)
            
            # If edits occur, save back to base state (minus the ETA column) and instantly recalculate
            if not edit_df.equals(display_df):
                st.session_state.p_df = edit_df.drop(columns=['ETA'])
                st.rerun()
                
            final_display_df = edit_df
        
        # 5. Export & Save 
        st.download_button("📥 Download Plan (CSV)", final_display_df.to_csv(index=False).encode('utf-8-sig'), "race_plan.csv", "text/csv", width="stretch")
        
        if st.session_state.logged_in:
            r_name = st.text_input("Race Name to Save")
            if st.button("💾 Save to Cloud", width="stretch") and r_name:
                databases.create_document(DB_ID, R_COL, ID.unique(), {
                    "email": st.session_state.email,
                    "race_name": r_name,
                    "plan_json": final_display_df.to_json(orient='records'),
                    "distance_km": float(t_dist),
                    "elevation_gain_m": int(t_gain),
                    "finish_time": total_time
                })
                st.success("Race Saved Successfully!")

# --- Saved Races Tab ---
if st.session_state.logged_in:
    with saved_tab:
        docs = databases.list_documents(DB_ID, R_COL, [Query.equal("email", st.session_state.email), Query.order_desc("$createdAt")])
        for d in docs['documents']:
            with st.expander(f"🏁 {d['race_name']} ({d['distance_km']:.1f}km)"):
                st.caption(f"Gain: {d['elevation_gain_m']}m | Time: {d['finish_time']}")
                rdf = pd.read_json(io.StringIO(d['plan_json']))
                
                # Cleanup older plans that might have saved the 'sec' column
                if 'sec' in rdf.columns: rdf = rdf.drop(columns=['sec'])
                
                st.dataframe(rdf, hide_index=True, width="stretch", column_config=cfg)
                
                c_dl, c_del = st.columns(2)
                c_dl.download_button("📥 Download CSV", rdf.to_csv(index=False).encode('utf-8-sig'), f"{d['race_name']}.csv", key=f"dl_{d['$id']}")
                if c_del.button("🗑️ Delete Race", key=f"del_{d['$id']}", width="stretch"):
                    databases.delete_document(DB_ID, R_COL, d['$id'])
                    st.rerun()

    # --- Admin Dashboard ---
    if admin_tab:
        with admin_tab:
            st.metric("Total Users", databases.list_documents(DB_ID, U_COL)['total'])
            st.metric("Total Plans", databases.list_documents(DB_ID, R_COL)['total'])
