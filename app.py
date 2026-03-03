import streamlit as st
import gpxpy
import pandas as pd
import plotly.express as px
import sqlite3
import json
from streamlit_google_auth import Authenticate

st.set_page_config(page_title="Trail Race Planner", layout="wide")

# --- 1. Database Setup (Simplified to just Email mapping) ---
def init_db():
    conn = sqlite3.connect('races.db')
    c = conn.cursor()
    # We no longer need a users table for passwords!
    # We just link saved races directly to the Google Email.
    c.execute('''CREATE TABLE IF NOT EXISTS saved_races
                 (email TEXT, race_name TEXT, plan_json TEXT)''')
    conn.commit()
    conn.close()

init_db()

# --- 2. Initialize Google Authenticator ---
# Make sure 'google_credentials.json' is in your project folder
if 'authenticator' not in st.session_state:
    st.session_state.authenticator = Authenticate(
        secret_credentials_path='google_credentials.json',
        cookie_name='trail_planner_auth',
        cookie_key='super_secret_key_change_me', # Change this to a random string!
        redirect_uri='https://trail-race-planner.streamlit.app'      # Update url of streamlit
    )

# Catch the login event when Google redirects back to the app
st.session_state.authenticator.check_authentification()

# --- 3. Sidebar: Google Login Flow ---
st.sidebar.title("Account")

if not st.session_state.get('connected', False):
    st.sidebar.write("Guest mode active. You can upload and export CSVs, but plans won't be saved.")
    st.sidebar.divider()
    st.sidebar.write("Log in to save your race plans.")
    
    # Renders the Google Login Button
    st.session_state.authenticator.login()
else:
    # User is logged in via Google! Extract their info.
    user_info = st.session_state['user_info']
    google_email = user_info.get('email')
    
    st.sidebar.image(user_info.get('picture'), width=50)
    st.sidebar.success(f"Welcome, {user_info.get('name')}!")
    
    if st.sidebar.button("Log Out"):
        st.session_state.authenticator.logout()
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

# Use Google 'connected' state to toggle features
is_logged_in = st.session_state.get('connected', False)

if is_logged_in:
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
        
        if is_logged_in:
            st.divider()
            st.subheader("💾 Save to Profile")
            race_name = st.text_input("Give this race a name (e.g., UTMB 2026)")
            if st.button("Save Race Plan"):
                if race_name:
                    conn = sqlite3.connect('races.db')
                    c = conn.cursor()
                    # Save the data using the Google email we extracted earlier
                    c.execute("INSERT INTO saved_races (email, race_name, plan_json) VALUES (?, ?, ?)", 
                              (google_email, race_name, edited_df.to_json(orient='records')))
                    conn.commit()
                    conn.close()
                    st.success(f"'{race_name}' saved successfully!")
                else:
                    st.warning("Please enter a race name.")

# --- 6. Saved Races View (Logged In Only) ---
if is_logged_in:
    with saved_tab:
        st.subheader("Your Saved Races")
        conn = sqlite3.connect('races.db')
        saved_data = pd.read_sql_query(f"SELECT race_name, plan_json FROM saved_races WHERE email='{google_email}'", conn)
        conn.close()
        
        if not saved_data.empty:
            for index, row in saved_data.iterrows():
                with st.expander(f"🏁 {row['race_name']}"):
                    reconstructed_df = pd.read_json(row['plan_json'], orient='records')
                    st.dataframe(reconstructed_df, hide_index=True, use_container_width=True)
        else:
            st.info("You haven't saved any races yet.")
