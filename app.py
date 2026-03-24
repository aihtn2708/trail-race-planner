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

# --- Safe Data Extractor ---
def get_val(obj, key, default=None):
    if isinstance(obj, dict):
        return obj.get(key, default)
    return getattr(obj, key, default)

# --- Security & Time Helpers ---
def hash_password(password): 
    return bcrypt.hashpw(password.encode('utf-8'), bcrypt.gensalt()).decode('utf-8')

# 🚀 FIX: Bulletproof password verification that catches empty or bad hashes
def verify_password(password, hashed_str):
    if not hashed_str or not isinstance(hashed_str, str):
        return False
    try:
        return bcrypt.checkpw(password.encode('utf-8'), hashed_str.encode('utf-8'))
    except ValueError:
        return False

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
    msg.set_content(f"Your temporary password is: {temp_password}")
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
                res = databases.list_documents(database_id=DB_ID, collection_id=U_COL, queries=[Query.equal("email", em)])
                total = get_val(res, 'total', 0)
                docs = get_val(res, 'documents', [])
                
                # It will safely fail here if the hash is missing
                if total > 0 and verify_password(pw, get_val(docs[0], 'password_hash', '')):
                    st.session_state.logged_in, st.session_state.email = True, em
                    st.rerun()
                else: 
                    st.error("Invalid email or password.")
                    
        with t[1]:
            rem = st.text_input("New Email", key="r_em")
            rpw = st.text_input("New Password", type="password", key="r_pw")
            if st.button("Create Account", width="stretch"):
                if len(rpw) >= 6:
                    exists = databases.list_documents(database_id=DB_ID, collection_id=U_COL, queries=[Query.equal("email", rem)])
                    if get_val(exists, 'total', 0) == 0:
                        databases.create_document(database_id=DB_ID, collection_id=U_COL, document_id=ID.unique(), data={"email": rem, "password_hash": hash_password(rpw)})
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
else:
    active_tab = st.container()

with active_tab:
    up = st.file_uploader("Upload GPX File")
    if up:
        if 'p_df' not in st.session_state or st.session_state.get('f_name') != up.name:
            p_df, r_df = process_gpx(up.getvalue())
            
            p_df['Pace'] = "06:00"
            p_df['💧'] = False
            p_df['🍯'] = False
            p_df['🍌'] = False
            p_df['🧂'] = False
            p_df['Notes'] = ""
            st.session_state.p_df, st.session_state.r_df, st.session_state.f_name = p_df, r_df, up.name
        
        base_df = st.session_state.p_df.copy()
        base_df['sec'] = base_df['Pace'].apply(pace_to_seconds)
        base_df['ETA'] = base_df['sec'].cumsum().apply(seconds_to_eta)
        display_df = base_df.drop(columns=['sec'])
        
        rdf = st.session_state.r_df
        t_dist = rdf['dist'].max() / 1000
        t_gain = display_df['gain'].sum()
        total_time = display_df['ETA'].iloc[-1]
        
        col1, col2, col3 = st.columns(3)
        col1.metric("Distance", f"{t_dist:.2f} km")
        col2.metric("Total Gain", f"{t_gain} m")
        col3.metric("Estimated Finish", total_time)

        st.plotly_chart(px.area(rdf, x='dist', y='ele', height=250), width="stretch")

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

        if is_mobile:
            st.info("📱 **Mobile View:** Swipe left/right on the table below to view all columns.")
            with st.form("mobile_edit"):
                f_km, t_km = st.columns(2)
                f_v = f_km.number_input("From KM", 1, int(display_df['km'].max()), 1)
                t_v = t_km.number_input("To KM", 1, int(display_df['km'].max()), int(display_df['km'].max()))
                p_v = st.text_input("New Pace (mm:ss)", "06:00")
                nutri = st.multiselect("Nutrition", ["💧", "🍯", "🍌", "🧂"], help="Select what to consume in this section")
                
                if st.form_submit_button("Apply Changes", width="stretch"):
                    mask = (st.session_state.p_df['km'] >= f_v) & (st.session_state.p_df['km'] <= t_v)
                    st.session_state.p_df.loc[mask, 'Pace'] = p_v
                    for icon in ["💧", "🍯", "🍌", "🧂"]:
                        st.session_state.p_df.loc[mask, icon] = (icon in nutri)
                    st.rerun()
            st.dataframe(display_df, hide_index=True, width="stretch", column_config=cfg)
            final_display_df = display_df
        else:
            st.info("💻 **Desktop View:** Click directly into the table cells below to edit your pace and nutrition.")
            edit_df = st.data_editor(display_df, hide_index=True, width="stretch", column_config=cfg)
            if not edit_df.equals(display_df):
                st.session_state.p_df = edit_df.drop(columns=['ETA'])
                st.rerun()
            final_display_df = edit_df
        
        st.download_button("📥 Download Plan (CSV)", final_display_df.to_csv(index=False).encode('utf-8-sig'), "race_plan.csv", "text/csv", width="stretch")
        
        if st.session_state.logged_in:
            r_name = st.text_input("Race Name to Save")
            if st.button("💾 Save to Cloud", width="stretch") and r_name:
                databases.create_document(
                    database_id=DB_ID, 
                    collection_id=R_COL, 
                    document_id=ID.unique(), 
                    data={
                        "email": st.session_state.email,
                        "race_name": r_name,
                        "plan_json": final_display_df.to_json(orient='records'),
                        "distance_km": float(t_dist),
                        "elevation_gain_m": int(t_gain),
                        "finish_time": total_time
                    }
                )
                st.success("Race Saved Successfully!")

# --- Saved Races Tab ---
if st.session_state.logged_in:
    with saved_tab:
        res = databases.list_documents(database_id=DB_ID, collection_id=R_COL, queries=[Query.equal("email", st.session_state.email), Query.order_desc("$createdAt")])
        docs = get_val(res, 'documents', [])
        
        for d in docs:
            r_name = get_val(d, 'race_name', 'Unknown')
            dist = get_val(d, 'distance_km', 0.0)
            gain = get_val(d, 'elevation_gain_m', 0)
            ftime = get_val(d, 'finish_time', 'N/A')
            p_json = get_val(d, 'plan_json', '[]')
            doc_id = get_val(d, '$id', get_val(d, 'id', ''))
            
            with st.expander(f"🏁 {r_name} ({dist:.1f}km)"):
                st.caption(f"Gain: {gain}m | Time: {ftime}")
                rdf = pd.read_json(io.StringIO(p_json))
                
                if 'sec' in rdf.columns: rdf = rdf.drop(columns=['sec'])
                
                st.dataframe(rdf, hide_index=True, width="stretch", column_config=cfg)
                
                c_dl, c_del = st.columns(2)
                c_dl.download_button("📥 Download CSV", rdf.to_csv(index=False).encode('utf-8-sig'), f"{r_name}.csv", key=f"dl_{doc_id}")
                if c_del.button("🗑️ Delete Race", key=f"del_{doc_id}", width="stretch"):
                    databases.delete_document(database_id=DB_ID, collection_id=R_COL, document_id=doc_id)
                    st.rerun()

    with settings_tab:
        st.subheader("🔐 Change Your Password")
        with st.form("change_password_form"):
            current_pwd = st.text_input("Current Password", type="password")
            new_pwd = st.text_input("New Password", type="password")
            confirm_pwd = st.text_input("Confirm New Password", type="password")
            if st.form_submit_button("Update Password", width="stretch"):
                res = databases.list_documents(database_id=DB_ID, collection_id=U_COL, queries=[Query.equal("email", st.session_state.email)])
                docs = get_val(res, 'documents', [])
                if docs:
                    stored_hash = get_val(docs[0], 'password_hash', '')
                    doc_id = get_val(docs[0], '$id', get_val(docs[0], 'id', ''))
                    if not verify_password(current_pwd, stored_hash): st.error("Current password incorrect.")
                    elif new_pwd != confirm_pwd: st.error("New passwords do not match.")
                    elif len(new_pwd) < 6: st.error("Must be at least 6 characters.")
                    else:
                        databases.update_document(database_id=DB_ID, collection_id=U_COL, document_id=doc_id, data={"password_hash": hash_password(new_pwd)})
                        st.success("Password updated!")

    # --- Admin Dashboard ---
    if admin_tab:
        with admin_tab:
            u_res = databases.list_documents(database_id=DB_ID, collection_id=U_COL)
            r_res = databases.list_documents(database_id=DB_ID, collection_id=R_COL)
            st.metric("Total Users", get_val(u_res, 'total', 0))
            st.metric("Total Plans", get_val(r_res, 'total', 0))
