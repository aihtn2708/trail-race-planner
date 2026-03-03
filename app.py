import streamlit as st
import gpxpy
import pandas as pd
import plotly.express as px
from datetime import datetime, timedelta

st.set_page_config(page_title="Trail Race Planner", layout="wide")

# --- 1. Data Processing Engine ---
@st.cache_data
def process_gpx(file_bytes):
    # Parse the GPX file
    gpx = gpxpy.parse(file_bytes)
    
    data = []
    cumulative_distance = 0
    previous_point = None
    
    # Extract points and calculate distance
    for track in gpx.tracks:
        for segment in track.segments:
            for point in segment.points:
                if previous_point:
                    dist = point.distance_2d(previous_point)
                    if dist:
                        cumulative_distance += dist
                
                data.append({
                    'distance_m': cumulative_distance,
                    'elevation': point.elevation
                })
                previous_point = point
                
    df = pd.DataFrame(data)
    
    # Calculate elevation changes point-by-point
    df['ele_diff'] = df['elevation'].diff().fillna(0)
    df['gain'] = df['ele_diff'].apply(lambda x: x if x > 0 else 0)
    df['loss'] = df['ele_diff'].apply(lambda x: abs(x) if x < 0 else 0)
    
    # Bucket into 1-Kilometer segments
    df['km_segment'] = (df['distance_m'] // 1000).astype(int) + 1
    
    # Aggregate data per KM
    plan_df = df.groupby('km_segment').agg(
        Gain_m=('gain', 'sum'),
        Loss_m=('loss', 'sum')
    ).reset_index()
    
    # Clean up numbers
    plan_df['Gain_m'] = plan_df['Gain_m'].round(0).astype(int)
    plan_df['Loss_m'] = plan_df['Loss_m'].round(0).astype(int)
    
    return plan_df, df

# --- 2. Helper for Time Math ---
def minutes_to_timedelta(minutes_str):
    try:
        return pd.to_timedelta(f"00:{minutes_str}:00")
    except:
        return pd.to_timedelta("00:06:00") # Default to 6 min/km if error

def format_timedelta(td):
    total_seconds = int(td.total_seconds())
    hours = total_seconds // 3600
    minutes = (total_seconds % 3600) // 60
    return f"{hours:02d}:{minutes:02d}"

# --- 3. User Interface ---
st.title("🏔️ Trail Race Planner")
st.markdown("Upload your race GPX file to generate an interactive pace and nutrition strategy.")

uploaded_file = st.file_uploader("Upload GPX File", type=["gpx"])

if uploaded_file is not None:
    # Process the file
    plan_df, raw_df = process_gpx(uploaded_file.getvalue())
    
    st.divider()
    
    # --- Top Level Metrics ---
    total_dist = raw_df['distance_m'].max() / 1000
    total_gain = plan_df['Gain_m'].sum()
    
    col1, col2, col3 = st.columns(3)
    col1.metric("Total Distance", f"{total_dist:.2f} km")
    col2.metric("Total Elevation Gain", f"{total_gain} m")
    col3.metric("Total Kilometers", f"{len(plan_df)}")

    # --- Elevation Profile Chart ---
    st.subheader("Course Profile")
    
    fig = px.area(raw_df, x='distance_m', y='elevation', 
                  labels={'distance_m': 'Distance (m)', 'elevation': 'Elevation (m)'})
    fig.update_layout(margin=dict(l=0, r=0, t=10, b=0), height=250)
    st.plotly_chart(fig, use_container_width=True)
    
    st.divider()
    
    # --- Race Strategy Table Setup ---
    st.subheader("Race Strategy & Config")
    
    # Add editable configuration columns with defaults
    plan_df.insert(0, 'KM', plan_df['km_segment'])
    plan_df = plan_df.drop('km_segment', axis=1)
    
    # Simple Grade Adjusted Pace logic: Default 6:00 flat, +1 min per 100m gain
    plan_df['Pace (min/km)'] = plan_df.apply(
        lambda row: f"{int(6 + (row['Gain_m'] / 100))}:00", axis=1
    )
    plan_df['Nutrition'] = "Water"
    plan_df['Notes'] = ""
    
    # --- Interactive Data Editor ---
    st.markdown("**Edit your pace, nutrition, and notes below. The ETA will calculate automatically.**")
    
    edited_df = st.data_editor(
        plan_df,
        column_config={
            "KM": st.column_config.NumberColumn(disabled=True),
            "Gain_m": st.column_config.NumberColumn("Gain (m)", disabled=True),
            "Loss_m": st.column_config.NumberColumn("Loss (m)", disabled=True),
            "Pace (min/km)": st.column_config.TextColumn("Pace (mm:ss)"),
            "Nutrition": st.column_config.SelectboxColumn(
                "Nutrition",
                options=["Water", "Gel", "Real Food", "Electrolytes", "Aid Station"]
            ),
            "Notes": st.column_config.TextColumn("Strategy / Notes")
        },
        hide_index=True,
        use_container_width=True
    )
    
    # --- Reactive Calculations ---
    # Calculate Split Times and cumulative ETA based on user edits
    split_timedeltas = edited_df['Pace (min/km)'].apply(minutes_to_timedelta)
    cumulative_time = split_timedeltas.cumsum()
    
    edited_df['ETA'] = cumulative_time.apply(format_timedelta)
    
    st.subheader("Final Race Plan Summary")
    st.dataframe(
        edited_df[['KM', 'Gain_m', 'Pace (min/km)', 'ETA', 'Nutrition', 'Notes']],
        hide_index=True, 
        use_container_width=True
    )
    
    # --- Export ---
    csv = edited_df.to_csv(index=False).encode('utf-8')
    st.download_button(
        label="Download Race Plan as CSV",
        data=csv,
        file_name='race_plan.csv',
        mime='text/csv',
    )
