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
from appwrite.query import Query
from appwrite.id import ID

# --- Page Configuration ---
st.set_page_config(page_title="Trail Race Planner", layout="wide", initial_sidebar_state="collapsed")

# --- Device Detection (Mobile vs Desktop) ---
def check_if_mobile():
    try:
        user_agent = st.context.headers.get("user-agent", "").lower()
        return any(keyword in user_agent for keyword in ['mobile', 'android', 'iphone', 'ipad'])
    except:
        return False
is_mobile = check_if_mobile()

# --- Initialize Appwrite Client ---
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
    st.error(f"Configuration Error: {e}")
    st.stop()

# --- Security & Email Helpers ---
def hash_password(password):
    return bcrypt.hashpw(password.encode('utf-8'), bcrypt.gensalt()).decode('utf-8')

def verify_password(password, hashed_str):
    return bcrypt.checkpw(password.encode('utf-8'), hashed_str.encode('utf-8'))

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

# --- Time Math Helpers ---
def pace_to_seconds(pace_str):
    try:
        m, s = map(int, str(pace_str).split(':'))
        return m * 60 + s
    except: return 360

def seconds_to_eta(total_sec):
    h, m = divmod(total_sec, 3600)
    m, s = divmod(m, 60)
    return f"{h:02d}:{m:02d}:{s:02d}"

# --- Authentication Logic ---
if 'logged_in' not in st.session_state: st.session_state.logged_in = False
if 'email' not in st.session_state: st.session_state.email = ""
if 'guest_mode' not in st.session_state: st.session_state.guest_mode = False

st.sidebar.title("Account Access")

if not st.session_state.logged_in:
    c1, c2 = st.sidebar.columns(2)
    with c1: 
        if st.button("👤 Guest", width="stretch"): st.session_state.guest_mode = True
    with c2: 
        if st.button("🔒 Login", width="stretch"): st.session_state.guest_mode = False

    if not st.session_state.guest_mode:
        auth_tabs = st.sidebar.tabs(["Login", "Sign Up"])
        with auth_tabs[0]:
            l_em = st.text_input("Email", key="l_em")
            l_pw = st.text_input("Password", type="password", key="l_pw")
            if st.button("Submit Login", width="stretch"):
                res = databases.list_documents(DB_ID, U_COL, [Query.equal("email", l_em)])
                if res['total'] > 0 and verify_password(l_pw, res['documents'][0]['password_hash']):
                    st.session_state.logged_in, st.session_state.email = True, l_em
                    st.rerun()
                else: st.error("Invalid credentials.")
            
            with st.expander("Forgot Password?"):
                r_em = st.text_input("Reset Email")
                if st.button("Send Temp Password"):
                    res = databases.list_documents(DB_ID, U_COL, [Query.equal("email", r_em)])
                    if res['total'] > 0:
                        temp = ''.join(random.choices(string.ascii_letters + string.digits, k=12))
                        databases.update_document(DB_ID, U_COL, res['documents'][0]['$id'], {"password_hash": hash_password(temp)})
                        st.info(f"Status: {send_reset_email(r_em, temp)}")
        
        with auth_tabs[1]:
            s_em = st.text_input("Email", key="s_em")
            s_pw = st.text_input("Password", type="password", key="s_pw")
            if st.button("Create Account", width="stretch"):
                if len(s_pw) >= 6 and re.match(r'[^@]+@[^@]+\.[^@]+', s_em):
                    exists = databases.list_documents(DB_ID, U_COL, [Query.equal("email", s_em)])
                    if exists['total'] == 0:
                        databases.create_document(DB_ID, U_COL, ID.unique(), {"email": s_em, "password_hash": hash_password(s_pw)})
                        st.success("Account created! Log in above.")
                    else: st.error("Email already registered.")
else:
    st.sidebar.success(f"User: {st.session_state.email}")
    if st.sidebar.button("Log Out", width="stretch"):
        st.session_state.logged_in = False
        st.rerun()

# --- Core GPX Processing ---
@st.cache_data
def process_gpx(file_bytes):
    gpx = gpxpy.parse(file_bytes)
    pts = []
    d_cum = 0
    prev = None
    for track in gpx.tracks:
        for segment in track.segments:
            for p in segment.points:
                if prev: d_cum += p.distance_2d(prev)
                pts.append({'dist': d_cum, 'ele': p.elevation})
                prev = p
    df = pd.DataFrame(pts)
    df['km'] = (df['dist'] // 1000).astype(int) + 1
    df['diff'] = df['ele'].diff().fillna(0)
    plan = df.groupby('km').agg(gain=('diff', lambda x: x[x>0].sum()), loss=('diff', lambda x: abs(x[x<0].sum()))).reset_index()
    return plan.round(0).astype(int), df

# --- Main UI ---
ADMIN_EMAIL = "aihtn2708@gmail.com"
st.title("🏔️ Trail Race Planner")

if st.session_state.logged_in:
    t_list = ["Plan New Race", "My Saved Races", "Account Settings"]
    if st.session_state.email == ADMIN_EMAIL: t_list.append("👑 Admin")
    app_tabs = st.tabs(t_list)
    active_tab, saved_tab, settings_tab = app_tabs[0], app_tabs[1], app_tabs[2]
    admin_tab = app_tabs[3] if len(app_tabs) > 3 else None
else:
    active_tab = st.container()

with active_tab:
    up = st.file_uploader("Upload GPX")
    if up:
        if 'p_df' not in st.session_state or st.session_state.f_name != up.name:
            p_df, r_df = process_gpx(up.getvalue())
            p_df['Pace'] = "06:00"
            st.session_state.p_df, st.session_state.r_df, st.session_state.f_name = p_df, r_df, up.name
        
        pdf, rdf = st.session_state.p_df, st.session_state.r_df
        t_dist = rdf['dist'].max() / 1000
        t_gain = pdf['gain'].sum()
        
        # Header Metrics
        m1, m2, m3 = st.columns(3)
        m1.metric("Distance", f"{t_dist:.2f} km")
        m2.metric("Gain", f"{t_gain} m")
        eta_placeholder = m3.empty()

        st.plotly_chart(px.area(rdf, x='dist', y='ele', height=250), width="stretch")

        # Dual UI Editor
        if is_mobile:
            with st.form("bulk_edit"):
                f_km = st.number_input("From KM", 1, int(pdf['km'].max()), 1)
                t_km = st.number_input("To KM", 1, int(pdf['km'].max()), int(pdf['km'].max()))
                new_p = st.text_input("New Pace (mm:ss)", "06:00")
                if st.form_submit_button("Apply Changes", width="stretch"):
                    pdf.loc[(pdf['km']>=f_km) & (pdf['km']<=t_km), 'Pace'] = new_p
                    st.session_state.p_df = pdf
                    st.rerun()
            edit_df = pdf
        else:
            edit_df = st.data_editor(pdf, hide_index=True, width="stretch", column_config={"km": st.column_config.NumberColumn(disabled=True)})
            st.session_state.p_df = edit_df

        # Final Calculations
        edit_df['sec'] = edit_df['Pace'].apply(pace_to_seconds)
        edit_df['ETA'] = edit_df['sec'].cumsum().apply(seconds_to_eta)
        eta_placeholder.metric("Estimated Finish", edit_df['ETA'].iloc[-1])

        st.dataframe(edit_df[['km', 'gain', 'loss', 'Pace', 'ETA']], hide_index=True, width="stretch")
        st.download_button("📥 Export CSV", edit_df.to_csv(index=False), "race_plan.csv", "text/csv", width="stretch")

        if st.session_state.logged_in:
            r_name = st.text_input("Save Race As...")
            if st.button("💾 Save to Appwrite", width="stretch") and r_name:
                databases.create_document(DB_ID, R_COL, ID.unique(), {
                    "email": st.session_state.email,
                    "race_name": r_name,
                    "plan_json": edit_df.to_json(orient='records'),
                    "distance_km": float(t_dist),
                    "elevation_gain_m": int(t_gain),
                    "finish_time": edit_df['ETA'].iloc[-1]
                })
                st.success("Race Saved Successfully!")

if st.session_state.logged_in:
    with saved_tab:
        docs = databases.list_documents(DB_ID, R_COL, [Query.equal("email", st.session_state.email), Query.order_desc("$createdAt")])
        for d in docs['documents']:
            with st.expander(f"🏁 {d['race_name']} — {d['distance_km']:.1f}km"):
                st.caption(f"Created: {d['$createdAt'][:10]} | Gain: {d['elevation_gain_m']}m | Finish: {d['finish_time']}")
                rdf = pd.read_json(io.StringIO(d['plan_json']))
                st.dataframe(rdf, hide_index=True, width="stretch")
                
                c_dl, c_del = st.columns(2)
                c_dl.download_button("📥 Download", rdf.to_csv(index=False), f"{d['race_name']}.csv", key=f"dl_{d['$id']}")
                if c_del.button("🗑️ Delete", key=f"del_{d['$id']}", width="stretch"):
                    databases.delete_document(DB_ID, R_COL, d['$id'])
                    st.rerun()

    if admin_tab:
        with admin_tab:
            st.metric("Total Users", databases.list_documents(DB_ID, U_COL)['total'])
            st.metric("Total Plans", databases.list_documents(DB_ID, R_COL)['total'])
            st.write("### Recent Activity")
            recent = databases.list_documents(DB_ID, R_COL, [Query.order_desc("$createdAt"), Query.limit(5)])
            st.table(pd.DataFrame(recent['documents'])[['email', 'race_name', 'distance_km']])
