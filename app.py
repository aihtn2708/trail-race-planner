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
from supabase import create_client, Client

st.set_page_config(page_title="Trail Race Planner", layout="wide", initial_sidebar_state="collapsed")

# --- Device Detection ---
def check_if_mobile():
    try:
        user_agent = st.context.headers.get("user-agent", "").lower()
        return any(keyword in user_agent for keyword in ['mobile', 'android', 'iphone', 'ipad'])
    except:
        return False
is_mobile = check_if_mobile()

# --- Load Secrets & Initialize Supabase ---
try:
    SENDER_EMAIL = st.secrets.get("SENDER_EMAIL", "")
    SENDER_APP_PASSWORD = st.secrets.get("SENDER_APP_PASSWORD", "")
    SUPABASE_URL = st.secrets["SUPABASE_URL"]
    SUPABASE_KEY = st.secrets["SUPABASE_KEY"]
    supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)
except KeyError as e:
    st.error(f"Missing Secret: {e}. Please configure your Streamlit Secrets.")
    st.stop()

# --- Time Math Helpers ---
# (Keep your existing pace_to_seconds and seconds_to_eta functions here)
def pace_to_seconds(pace_str):
    try:
        parts = str(pace_str).split(':')
        return int(parts[0]) * 60 + int(parts[1])
    except:
        return 360

def seconds_to_eta(total_seconds):
    hours = total_seconds // 3600
    minutes = (total_seconds % 3600) // 60
    seconds = total_seconds % 60
    return f"{hours:02d}:{minutes:02d}:{seconds:02d}"

# --- 1. Supabase Database & Security Functions ---
def hash_password(password):
    # bcrypt returns bytes, Supabase needs a string. So we decode it.
    return bcrypt.hashpw(password.encode('utf-8'), bcrypt.gensalt()).decode('utf-8')

def verify_password(password, hashed_str):
    # Supabase returns a string, bcrypt needs bytes to verify. So we encode it.
    return bcrypt.checkpw(password.encode('utf-8'), hashed_str.encode('utf-8'))

def is_valid_email(email):
    return re.match(r'^[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+$', email) is not None

def generate_temp_password():
    chars = string.ascii_letters + string.digits + "!@#$%^&*"
    return ''.join(random.choice(chars) for i in range(12))

def send_reset_email(to_email, temp_password):
    if not SENDER_EMAIL or not SENDER_APP_PASSWORD:
        return "SIMULATED"
    msg = EmailMessage()
    msg.set_content(f"Your temporary password is: {temp_password}\n\nPlease log in and update your password immediately.")
    msg['Subject'] = 'Password Reset - Trail Race Planner'
    msg['From'] = SENDER_EMAIL
    msg['To'] = to_email
    try:
        server = smtplib.SMTP_SSL('smtp.gmail.com', 465)
        server.login(SENDER_EMAIL, SENDER_APP_PASSWORD)
        server.send_message(msg)
        server.quit()
        return "SUCCESS"
    except Exception as e:
        return f"ERROR: {e}"

def update_user_password(email, new_password):
    new_hash = hash_password(new_password)
    supabase.table('users').update({'password_hash': new_hash}).eq('email', email).execute()

# --- 2. State Initialization ---
if 'logged_in' not in st.session_state:
    st.session_state.logged_in = False
    st.session_state.email = ""
if 'guest_mode' not in st.session_state:
    st.session_state.guest_mode = False

# --- 3. Sidebar: Authentication UI ---
st.sidebar.title("Account Access")

if not st.session_state.logged_in:
    col1, col2 = st.sidebar.columns(2)
    with col1:
        if st.button("👤 Guest", width="stretch"):
            st.session_state.guest_mode = True
    with col2:
        if st.button("🔒 Log In", width="stretch"):
            st.session_state.guest_mode = False

    if st.session_state.guest_mode:
        st.sidebar.info("✨ **Guest Mode Active**\nYou can plan races, but saving requires an account.")
    else:
        auth_tabs = st.sidebar.tabs(["Log In", "Sign Up"])
        
        with auth_tabs[0]:
            login_email = st.text_input("Email", key="log_email")
            login_pwd = st.text_input("Password", type="password", key="log_pwd")
            
            if st.button("Submit Login"):
                # Supabase Query: Get user by email
                res = supabase.table('users').select('password_hash').eq('email', login_email).execute()
                
                if len(res.data) > 0 and verify_password(login_pwd, res.data[0]['password_hash']):
                    st.session_state.logged_in = True
                    st.session_state.email = login_email
                    st.rerun()
                else:
                    st.error("Invalid email or password.")
            
            with st.expander("Forgot Password?"):
                reset_email = st.text_input("Enter your account email")
                if st.button("Reset Password"):
                    res = supabase.table('users').select('email').eq('email', reset_email).execute()
                    if len(res.data) > 0:
                        temp_pwd = generate_temp_password()
                        update_user_password(reset_email, temp_pwd)
                        
                        email_status = send_reset_email(reset_email, temp_pwd)
                        if email_status == "SUCCESS":
                            st.success("A temporary password has been sent to your email.")
                        elif email_status == "SIMULATED":
                            st.warning(f"Email config missing. Your temp password is: **{temp_pwd}**")
                        else:
                            st.error(f"Failed to send email: {email_status}")
                    else:
                        st.error("Email not found in database.")

        with auth_tabs[1]:
            reg_email = st.text_input("Email", key="reg_email")
            reg_pwd = st.text_input("Password", type="password", key="reg_pwd")
            
            if st.button("Create Account"):
                if not is_valid_email(reg_email):
                    st.error("Enter a valid email.")
                elif len(reg_pwd) < 6:
                    st.error("Password must be at least 6 characters.")
                else:
                    # Supabase Query: Check if user already exists
                    existing = supabase.table('users').select('email').eq('email', reg_email).execute()
                    if len(existing.data) > 0:
                        st.error("An account with this email already exists.")
                    else:
                        supabase.table('users').insert({
                            'email': reg_email, 
                            'password_hash': hash_password(reg_pwd)
                        }).execute()
                        st.success("Account created! Please log in.")

else:
    st.sidebar.success(f"Logged in as:\n**{st.session_state.email}**")
    if st.sidebar.button("Log Out", width="stretch"):
        st.session_state.logged_in = False
        st.session_state.email = ""
        st.session_state.guest_mode = False
        st.rerun()

# --- 4. Core GPX Processing Engine ---
# (Keep your existing process_gpx function here entirely unmodified)
@st.cache_data
def process_gpx(file_bytes):
    gpx = gpxpy.parse(file_bytes)
    data = []
    cum_dist = 0
    prev_point = None
    for track in gpx.tracks:
        for segment in track.segments:
            for point in segment.points:
                if prev_point:
                    dist = point.distance_2d(prev_point)
                    if dist: cum_dist += dist
                data.append({'distance_m': cum_dist, 'elevation': point.elevation})
                prev_point = point
    df = pd.DataFrame(data)
    df['ele_diff'] = df['elevation'].diff().fillna(0)
    df['gain'] = df['ele_diff'].apply(lambda x: x if x > 0 else 0)
    df['loss'] = df['ele_diff'].apply(lambda x: abs(x) if x < 0 else 0)
    df['km_segment'] = (df['distance_m'] // 1000).astype(int) + 1
    plan_df = df.groupby('km_segment').agg(Gain_m=('gain', 'sum'), Loss_m=('loss', 'sum')).reset_index()
    plan_df['Gain_m'] = plan_df['Gain_m'].round(0).astype(int)
    plan_df['Loss_m'] = plan_df['Loss_m'].round(0).astype(int)
    return plan_df, df

# --- 5. Main App UI ---
ADMIN_EMAIL = "aihtn2708@gmail.com"
st.title("🏔️ Trail Race Planner")

if st.session_state.logged_in:
    if st.session_state.email == ADMIN_EMAIL:
        app_tabs = st.tabs(["Plan New Race", "My Saved Races", "Account Settings", "👑 Admin"])
        admin_tab = app_tabs[3]
    else:
        app_tabs = st.tabs(["Plan New Race", "My Saved Races", "Account Settings"])
        admin_tab = None
    active_tab = app_tabs[0]
    saved_tab = app_tabs[1]
    settings_tab = app_tabs[2]
else:
    active_tab = st.container()

with active_tab:
    # (Keep your entire GPX upload, Course Profile, and Dual UI Logic here)
    # The only change in this tab is replacing the SQLite INSERT logic in the "Save" button:
    
    # ... [YOUR EXISTING UI CODE] ...
    
        # --- SUPABASE SAVE FEATURE UPDATE ---
        if st.session_state.logged_in:
            st.divider()
            st.subheader("💾 Save to Profile")
            race_name = st.text_input("Give this race a name (e.g., UTMB 2026)")
            if st.button("Save Race Plan", width="stretch"):
                if race_name:
                    # Replace SQLite insert with Supabase insert
                    try:
                        supabase.table('saved_races').insert({
                            'email': st.session_state.email,
                            'race_name': race_name,
                            'plan_json': final_display_df.to_json(orient='records')
                        }).execute()
                        st.success(f"'{race_name}' saved successfully to cloud!")
                    except Exception as e:
                        st.error(f"Failed to save to cloud: {e}")
                else:
                    st.warning("Please enter a race name.")

if st.session_state.logged_in:
    with saved_tab:
        st.subheader("Your Saved Races")
        
        # --- SUPABASE RETRIEVE FEATURE UPDATE ---
        res = supabase.table('saved_races').select('*').eq('email', st.session_state.email).order('id', desc=True).execute()
        saved_data = pd.DataFrame(res.data)
        
        if not saved_data.empty:
            for index, row in saved_data.iterrows():
                with st.expander(f"🏁 {row['race_name']}"):
                    reconstructed_df = pd.read_json(io.StringIO(row['plan_json']), orient='records')
                    if is_mobile:
                        st.dataframe(reconstructed_df, hide_index=True, width="stretch", column_config={
                            "KM": st.column_config.NumberColumn("KM", width="small"),
                            "Gain_m": st.column_config.NumberColumn("🔺", width="small"),
                            "Loss_m": st.column_config.NumberColumn("🔻", width="small"),
                            "Pace (mm:ss)": st.column_config.TextColumn("Pace", width="small"),
                            "ETA": st.column_config.TextColumn("ETA", width="small"),
                            "💧 Water": st.column_config.CheckboxColumn("💧", width="small"),
                            "🍯 Gel": st.column_config.CheckboxColumn("🍯", width="small"),
                            "🍌 Food": st.column_config.CheckboxColumn("🍌", width="small"),
                            "🧂 Salt": st.column_config.CheckboxColumn("🧂", width="small"),
                        })
                    else:
                        st.dataframe(reconstructed_df, hide_index=True, width="stretch")
        else:
            st.info("You haven't saved any races yet.")
            
    with settings_tab:
        st.subheader("🔐 Change Your Password")
        st.info("If you logged in with a temporary password, please update it below.")
        with st.form("change_password_form"):
            current_pwd = st.text_input("Current Password", type="password")
            new_pwd = st.text_input("New Password", type="password")
            confirm_pwd = st.text_input("Confirm New Password", type="password")
            if st.form_submit_button("Update Password", width="stretch"):
                res = supabase.table('users').select('password_hash').eq('email', st.session_state.email).execute()
                stored_hash = res.data[0]['password_hash']
                
                if not verify_password(current_pwd, stored_hash):
                    st.error("The current password you entered is incorrect.")
                elif new_pwd != confirm_pwd:
                    st.error("New passwords do not match.")
                elif len(new_pwd) < 6:
                    st.error("New password must be at least 6 characters long.")
                else:
                    update_user_password(st.session_state.email, new_pwd)
                    st.success("Password updated successfully! Use your new password next time you log in.")

    # --- SUPABASE ADMIN DASHBOARD ---
    if admin_tab:
        with admin_tab:
            st.subheader("App Performance Metrics")
            
            # Fetch exact counts using Supabase built-in count feature
            users_res = supabase.table('users').select('*', count='exact').execute()
            plans_res = supabase.table('saved_races').select('*', count='exact').execute()
            top_races_res = supabase.table('saved_races').select('email, race_name').order('id', desc=True).limit(5).execute()
            
            col1, col2 = st.columns(2)
            col1.metric("👥 Total Registered Users", users_res.count)
            col2.metric("💾 Total Saved Race Plans", plans_res.count)
            
            st.divider()
            st.write("**Recent App Activity (Last 5 Plans Created):**")
            st.dataframe(pd.DataFrame(top_races_res.data), hide_index=True, width="stretch")
