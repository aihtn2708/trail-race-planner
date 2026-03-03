import streamlit as st
import gpxpy
import pandas as pd
import plotly.express as px
import sqlite3
import json

st.set_page_config(page_title="Trail Race Planner", layout="wide")

# --- 1. Database Setup for Saving Plans ---
def init_db():
    conn = sqlite3.connect('races.db')
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS saved_races
                 (username TEXT, race_name TEXT, plan_json TEXT)''')
    conn.commit()
    conn.close()

init_db()

def save_race_to_db(username, race_name, df):
    conn = sqlite3.connect('races.db')
    c = conn.cursor()
    # Convert dataframe to JSON for easy storage
    plan_json = df.to_json(orient='records')
    c.execute("INSERT INTO saved_races VALUES (?, ?, ?)", (username, race_name, plan_json))
    conn.commit()
    conn.close()

# --- 2. Authentication State ---
if 'logged_in' not in st.session_state:
    st.session_state.logged_in = False
    st.session_state.username = ""

# --- 3. Sidebar: Login vs Guest Mode ---
st.sidebar.title("Account")

if not st.session_state.logged_in:
    auth_tabs = st.sidebar.tabs(["Continue as Guest", "Login"])
    
    with auth_tabs[0]:
        st.write("Guest mode active. You can upload and export CSVs, but plans won't be saved.")
        
    with auth_tabs[1]:
        st.write("Log in to save your race plans.")
        user_input = st.text_input("Username")
        pwd_input = st.text_input("Password", type="password")
        if st.button("Log In"):
            # Simple mock login for demonstration
            if user_input: 
                st.session_state.logged_in = True
                st.session_state.username = user_input
                st.rerun()
else:
    st.sidebar.success(f"Welcome back, {st.session_state.username}!")
    if st.sidebar.button("Log Out"):
        st.session_state.logged_in = False
        st.session_state.username = ""
        st.rerun()

# --- 4. Core GPX Processing (Simplified from previous) ---
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

# If logged in, use tabs to separate "New Planner" and "Saved Races"
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
        
        # Course Profile Graph
        st.subheader("Course Profile")
        fig = px.area(raw_df, x='distance_m', y='elevation', labels={'distance_m': 'Distance (m)', 'elevation': 'Elevation (m)'})
        st.plotly_chart(fig, use_container_width=True)
        
        # Strategy Table
        st.subheader("Race Strategy")
        plan_df.insert(0, 'KM', plan_df['km_segment'])
        plan_df = plan_df.drop('km_segment', axis=1)
        plan_df['Pace (min/km)'] = "06:00" 
        plan_df['Nutrition'] = "Water"
        plan_df['Notes'] = ""
        
        edited_df = st.data_editor(plan_df, hide_index=True, use_container_width=True)
        
        # Download CSV (Available to everyone)
        st.download_button(
            label="Download as CSV",
            data=edited_df.to_csv(index=False).encode('utf-8'),
            file_name='race_plan.csv',
            mime='text/csv'
        )
        
        # SAVE FEATURE (Only available if logged in)
        if st.session_state.logged_in:
            st.divider()
            st.subheader("💾 Save to Profile")
            race_name = st.text_input("Give this race a name (e.g., UTMB 2026)")
            if st.button("Save Race Plan"):
                if race_name:
                    save_race_to_db(st.session_state.username, race_name, edited_df)
                    st.success(f"'{race_name}' saved successfully!")
                else:
                    st.warning("Please enter a race name.")

# --- 6. Saved Races View (Only for Logged In Users) ---
if st.session_state.logged_in:
    with saved_tab:
        st.subheader("Your Saved Races")
        conn = sqlite3.connect('races.db')
        saved_data = pd.read_sql_query(f"SELECT race_name, plan_json FROM saved_races WHERE username='{st.session_state.username}'", conn)
        conn.close()
        
        if not saved_data.empty:
            for index, row in saved_data.iterrows():
                with st.expander(f"🏁 {row['race_name']}"):
                    # Reconstruct dataframe from saved JSON
                    reconstructed_df = pd.read_json(row['plan_json'], orient='records')
                    st.dataframe(reconstructed_df, hide_index=True, use_container_width=True)
        else:
            st.info("You haven't saved any races yet.")
