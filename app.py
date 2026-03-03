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

st.set_page_config(page_title="Trail Race Planner", layout="wide")

# --- Configuration for Email Sending ---
# To send real emails, put your Gmail address and a 16-character "App Password" here.
# (You generate App Passwords in your Google Account Security settings).
SENDER_EMAIL = "aihtn2708@gmail.com" 
SENDER_APP_PASSWORD = "hrph tlsh ysxg leti" 

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
        # Fallback for testing if email is not configured yet
        return "SIMULATED"
        
    msg = EmailMessage()
    msg.set_content(f"Your temporary password is: {temp_password}\n\nPlease log in and update your password.")
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

# --- 2. State Initialization ---
if 'logged_in' not in st.session_state:
    st.session_state.logged_in = False
    st.session_state.email = ""
if 'guest_mode' not in st.session_state:
    st.session_state.guest_mode = False

# --- 3. Sidebar: Authentication UI ---
st.sidebar.title("Account Access")

if not st.session_state.logged_in:
    # Side-by-side Guest / Login layout
    col1, col2 = st.sidebar.columns(2)
    with col1:
        if st.button("👤 Guest", use_container_width=True):
            st.session_state.guest_mode = True
            
    with col2:
        if st.button("🔒 Log In", use_container_width=True):
            st.session_state.guest_mode = False

    if st.session_state.guest_mode:
        st.sidebar.info("✨ **Guest Mode Active**\nYou can plan races, but saving requires an account.")
    else:
        auth_tabs = st.sidebar.tabs(["Log In", "Sign Up"])
        
        # --- LOG IN TAB ---
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
            
            # --- FORGOT PASSWORD EXPANDER ---
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
                            st.error("Failed to send email. Check logs.")
                    else:
                        st.error("Email not found in database.")
                    conn.close()

        # --- SIGN UP TAB ---
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
    # Logged In View
    st.sidebar.success(f"Logged in as:\n**{st.session_state.email}**")
    if st.sidebar.button("Log Out", use_container_width=True):
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
    app_tabs = st.tabs(["Plan New Race", "My Saved Races"])
    active_tab = app_tabs[0]
    saved_tab = app_tabs[1]
else:
    active_tab = st.container()

with active_tab:
    uploaded_file = st.file_uploader("Upload GPX File", type=["gpx"])

    if uploaded_file is not None:
        plan_df, raw_df = process_gpx(uploaded_file.getvalue())
        
        st.subheader("Course Profile")
        fig = px.area(raw_df, x='distance_m', y='elevation', labels={'distance_m': 'Distance (m)', 'elevation': 'Elevation (m)'})
        st.plotly_chart(fig, use_container_width=True)
        
        st.subheader("Race Strategy")
        plan_df.insert(0, 'KM', plan_df['km_segment'])
        plan_df = plan_df.drop('km_segment', axis=1)
        plan_df['Pace (min/km)'] = "06:00" 
        plan_df['Nutrition'] = "Water"
        plan_df['Notes'] = ""
        
        edited_df = st.data_editor(plan_df, hide_index=True, use_container_width=True)
        
        if st.session_state.logged_in:
            st.divider()
            st.subheader("💾 Save to Profile")
            race_name = st.text_input("Give this race a name (e.g., UTMB 2026)")
            if st.button("Save Race Plan"):
                if race_name:
                    conn = sqlite3.connect('races.db')
                    c = conn.cursor()
                    c.execute("INSERT INTO saved_races (email, race_name, plan_json) VALUES (?, ?, ?)", 
                              (st.session_state.email, race_name, edited_df.to_json(orient='records')))
                    conn.commit()
                    conn.close()
                    st.success(f"'{race_name}' saved successfully!")
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
                    reconstructed_df = pd.read_json(row['plan_json'], orient='records')
                    st.dataframe(reconstructed_df, hide_index=True, use_container_width=True)
        else:
            st.info("You haven't saved any races yet.")
