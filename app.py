import streamlit as st
import gpxpy
import pandas as pd
import plotly.express as px
import sqlite3
import re
import bcrypt
import smtplib
from email.message import EmailMessage
import random
import string
import io  

st.set_page_config(page_title="Trail Race Planner", layout="wide")

# --- Configuration for Email Sending (Using Streamlit Secrets) ---
try:
    SENDER_EMAIL = st.secrets["SENDER_EMAIL"]
    SENDER_APP_PASSWORD = st.secrets["SENDER_APP_PASSWORD"]
except (FileNotFoundError, KeyError):
    SENDER_EMAIL = ""
    SENDER_APP_PASSWORD = ""

# --- Time Math Helpers ---
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

# --- 1. Database & Security Functions ---
def init_db():
    conn = sqlite3.connect('races.db')
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS users (email TEXT PRIMARY KEY, password_hash TEXT)''')
    c.execute('''CREATE TABLE IF NOT EXISTS saved_races (email TEXT, race_name TEXT, plan_json TEXT)''')
    conn.commit()
    conn.close()

init_db()

def hash_password(password):
    return bcrypt.hashpw(password.encode('utf-8'), bcrypt.gensalt())

def verify_password(password, hashed):
    return bcrypt.checkpw(password.encode('utf-8'), hashed)

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
    conn = sqlite3.connect('races.db')
    c = conn.cursor()
    new_hash = hash_password(new_password)
    c.execute("UPDATE users SET password_hash=? WHERE email=?", (new_hash, email))
    conn.commit()
    conn.close()

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
        # FIXED: width="stretch"
        if st.button("👤 Guest", width="stretch"):
            st.session_state.guest_mode = True
            
    with col2:
        # FIXED: width="stretch"
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
                conn = sqlite3.connect('races.db')
                c = conn.cursor()
                c.execute("SELECT password_hash FROM users WHERE email=?", (login_email,))
                result = c.fetchone()
                conn.close()
                
                if result and verify_password(login_pwd, result[0]):
                    st.session_state.logged_in = True
                    st.session_state.email = login_email
                    st.rerun()
                else:
                    st.error("Invalid email or password.")
            
            with st.expander("Forgot Password?"):
                reset_email = st.text_input("Enter your account email")
                if st.button("Reset Password"):
                    conn = sqlite3.connect('races.db')
                    c = conn.cursor()
                    c.execute("SELECT email FROM users WHERE email=?", (reset_email,))
                    if c.fetchone():
                        temp_pwd = generate_temp_password()
                        c.execute("UPDATE users SET password_hash=? WHERE email=?", (hash_password(temp_pwd), reset_email))
                        conn.commit()
                        
                        email_status = send_reset_email(reset_email, temp_pwd)
                        if email_status == "SUCCESS":
                            st.success("A temporary password has been sent to your email.")
                        elif email_status == "SIMULATED":
                            st.warning(f"Email config missing. Your temp password is: **{temp_pwd}**")
                        else:
                            st.error(f"Failed to send email: {email_status}")
                    else:
                        st.error("Email not found in database.")
                    conn.close()

        with auth_tabs[1]:
            reg_email = st.text_input("Email", key="reg_email")
            reg_pwd = st.text_input("Password", type="password", key="reg_pwd")
            
            if st.button("Create Account"):
                if not is_valid_email(reg_email):
                    st.error("Enter a valid email.")
                elif len(reg_pwd) < 6:
                    st.error("Password must be at least 6 characters.")
                else:
                    conn = sqlite3.connect('races.db')
                    c = conn.cursor()
                    try:
                        c.execute("INSERT INTO users (email, password_hash) VALUES (?, ?)", 
                                  (reg_email, hash_password(reg_pwd)))
                        conn.commit()
                        st.success("Account created! Please log in.")
                    except sqlite3.IntegrityError:
                        st.error("An account with this email already exists.")
                    finally:
                        conn.close()

else:
    st.sidebar.success(f"Logged in as:\n**{st.session_state.email}**")
    # FIXED: width="stretch"
    if st.sidebar.button("Log Out", width="stretch"):
        st.session_state.logged_in = False
        st.session_state.email = ""
        st.session_state.guest_mode = False
        st.rerun()

# --- 4. Core GPX Processing Engine ---
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
st.title("🏔️ Trail Race Planner")

if st.session_state.logged_in:
    app_tabs = st.tabs(["Plan New Race", "My Saved Races", "Account Settings"])
    active_tab = app_tabs[0]
    saved_tab = app_tabs[1]
    settings_tab = app_tabs[2]
else:
    active_tab = st.container()

with active_tab:
    uploaded_file = st.file_uploader("Upload GPX File", type=["gpx"])

    if uploaded_file is not None:
        plan_df, raw_df = process_gpx(uploaded_file.getvalue())
        
        st.subheader("Race Summary")
        total_dist = raw_df['distance_m'].max() / 1000
        total_gain = plan_df['Gain_m'].sum()
        
        col1, col2, col3 = st.columns(3)
        col1.metric("Total Distance", f"{total_dist:.2f} km")
        col2.metric("Total Elevation Gain", f"{total_gain} m")
        eta_metric = col3.empty()
        
        st.subheader("Course Profile")
        fig = px.area(raw_df, x='distance_m', y='elevation', labels={'distance_m': 'Distance (m)', 'elevation': 'Elevation (m)'})
        fig.update_layout(margin=dict(l=0, r=0, t=10, b=0), height=250)
        # FIXED: width="stretch"
        st.plotly_chart(fig, width="stretch")
        
        st.subheader("Race Strategy")
        
        plan_df.insert(0, 'KM', plan_df['km_segment'])
        plan_df = plan_df.drop('km_segment', axis=1)
        plan_df['Pace (mm:ss)'] = "06:00" 
        plan_df['💧 Water'] = False
        plan_df['🍯 Gel'] = False
        plan_df['🍌 Food'] = False
        plan_df['🧂 Salt'] = False
        plan_df['Notes'] = ""
        
        st.markdown("**Edit your pace and check off your nutrition plan. The ETA will update automatically.**")
        
        # FIXED: width="stretch"
        edited_df = st.data_editor(
            plan_df, 
            column_config={
                "KM": st.column_config.NumberColumn(disabled=True),
                "Gain_m": st.column_config.NumberColumn("Gain (m)", disabled=True),
                "Loss_m": st.column_config.NumberColumn("Loss (m)", disabled=True),
                "Pace (mm:ss)": st.column_config.TextColumn("Pace (mm:ss)"),
            },
            hide_index=True, 
            width="stretch"
        )
        
        edited_df['pace_sec'] = edited_df['Pace (mm:ss)'].apply(pace_to_seconds)
        edited_df['cum_sec'] = edited_df['pace_sec'].cumsum()
        edited_df['ETA'] = edited_df['cum_sec'].apply(seconds_to_eta)
        
        total_finish_time = edited_df['ETA'].iloc[-1] if not edited_df.empty else "00:00:00"
        eta_metric.metric("Estimated Finish Time", total_finish_time)
        
        cols = ['KM', 'Gain_m', 'Loss_m', 'Pace (mm:ss)', 'ETA', '💧 Water', '🍯 Gel', '🍌 Food', '🧂 Salt', 'Notes']
        final_display_df = edited_df[cols]
        
        if st.session_state.logged_in:
            st.divider()
            st.subheader("💾 Save to Profile")
            race_name = st.text_input("Give this race a name (e.g., UTMB 2026)")
            if st.button("Save Race Plan"):
                if race_name:
                    conn = sqlite3.connect('races.db')
                    c = conn.cursor()
                    c.execute("INSERT INTO saved_races (email, race_name, plan_json) VALUES (?, ?, ?)", 
                              (st.session_state.email, race_name, final_display_df.to_json(orient='records')))
                    conn.commit()
                    conn.close()
                    st.success(f"'{race_name}' saved successfully with a finish time of {total_finish_time}!")
                else:
                    st.warning("Please enter a race name.")

if st.session_state.logged_in:
    with saved_tab:
        st.subheader("Your Saved Races")
        conn = sqlite3.connect('races.db')
        saved_data = pd.read_sql_query("SELECT race_name, plan_json FROM saved_races WHERE email=?", conn, params=(st.session_state.email,))
        conn.close()
        
        if not saved_data.empty:
            for index, row in saved_data.iterrows():
                with st.expander(f"🏁 {row['race_name']}"):
                    # FIXED: Wrapped the JSON string in io.StringIO() to prevent FutureWarnings
                    reconstructed_df = pd.read_json(io.StringIO(row['plan_json']), orient='records')
                    # FIXED: width="stretch"
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
            
            submit_change = st.form_submit_button("Update Password")
            
            if submit_change:
                conn = sqlite3.connect('races.db')
                c = conn.cursor()
                c.execute("SELECT password_hash FROM users WHERE email=?", (st.session_state.email,))
                stored_hash = c.fetchone()[0]
                conn.close()
                
                if not verify_password(current_pwd, stored_hash):
                    st.error("The current password you entered is incorrect.")
                elif new_pwd != confirm_pwd:
                    st.error("New passwords do not match.")
                elif len(new_pwd) < 6:
                    st.error("New password must be at least 6 characters long.")
                else:
                    update_user_password(st.session_state.email, new_pwd)
                    st.success("Password updated successfully! Use your new password next time you log in.")
