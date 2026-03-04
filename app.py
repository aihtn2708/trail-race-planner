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

# --- Load Secrets & Initialize Supabase ---
try:
    SENDER_EMAIL = st.secrets.get("SENDER_EMAIL", "")
    SENDER_APP_PASSWORD = st.secrets.get("SENDER_APP_PASSWORD", "")
    SUPABASE_URL = st.secrets["SUPABASE_URL"]
    SUPABASE_KEY = st.secrets["SUPABASE_KEY"]
    supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)
except KeyError as e:
    st.error(f"Missing Secret: {e}. Please configure your Streamlit Secrets (.streamlit/secrets.toml).")
    st.stop()

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

# --- 1. Supabase Database & Security Functions ---
def hash_password(password):
    return bcrypt.hashpw(password.encode('utf-8'), bcrypt.gensalt()).decode('utf-8')

def verify_password(password, hashed_str):
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
        st.sidebar.info("✨ **Guest Mode Active**\nYou can plan and export races, but saving requires an account.")
    else:
        auth_tabs = st.sidebar.tabs(["Log In", "Sign Up"])
        
        with auth_tabs[0]:
            login_email = st.text_input("Email", key="log_email")
            login_pwd = st.text_input("Password", type="password", key="log_pwd")
            
            if st.button("Submit Login", width="stretch"):
                res = supabase.table('users').select('password_hash').eq('email', login_email).execute()
                if len(res.data) > 0 and verify_password(login_pwd, res.data[0]['password_hash']):
                    st.session_state.logged_in = True
                    st.session_state.email = login_email
                    st.rerun()
                else:
                    st.error("Invalid email or password.")
            
            with st.expander("Forgot Password?"):
                reset_email = st.text_input("Enter your account email")
                if st.button("Reset Password", width="stretch"):
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
            
            if st.button("Create Account", width="stretch"):
                if not is_valid_email(reg_email):
                    st.error("Enter a valid email.")
                elif len(reg_pwd) < 6:
                    st.error("Password must be at least 6 characters.")
                else:
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
ADMIN_EMAIL = "aihtn2708@gmail.com" # <-- Update this to your real email!
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
    uploaded_file = st.file_uploader("Upload GPX File")

    if uploaded_file is not None:
        if 'last_file' not in st.session_state or st.session_state.last_file != uploaded_file.name:
            plan_df, raw_df = process_gpx(uploaded_file.getvalue())
            plan_df.insert(0, 'KM', plan_df['km_segment'])
            plan_df = plan_df.drop('km_segment', axis=1)
            plan_df['Pace (mm:ss)'] = "06:00" 
            plan_df['💧 Water'] = False
            plan_df['🍯 Gel'] = False
            plan_df['🍌 Food'] = False
            plan_df['🧂 Salt'] = False
            plan_df['Notes'] = ""
            
            st.session_state.race_plan = plan_df
            st.session_state.raw_df = raw_df
            st.session_state.last_file = uploaded_file.name
            
        df = st.session_state.race_plan
        raw_df = st.session_state.raw_df
        
        st.subheader("Race Summary")
        total_dist = raw_df['distance_m'].max() / 1000
        total_gain = df['Gain_m'].sum()
        
        col1, col2, col3 = st.columns(3)
        col1.metric("Total Distance", f"{total_dist:.2f} km")
        col2.metric("Total Elevation Gain", f"{total_gain} m")
        eta_metric = col3.empty()
        
        st.subheader("Course Profile")
        fig = px.area(raw_df, x='distance_m', y='elevation', labels={'distance_m': 'Distance (m)', 'elevation': 'Elevation (m)'})
        fig.update_layout(margin=dict(l=0, r=0, t=10, b=0), height=250)
        st.plotly_chart(fig, width="stretch")
        
        st.subheader("Race Strategy")
        
        # --- 🔀 DUAL UI ROUTING ---
        if is_mobile:
            st.info("📱 **Mobile View:** Update multiple kilometers at once using the form below.")
            with st.form("bulk_edit_form"):
                col_k1, col_k2 = st.columns(2)
                max_km = int(df['KM'].max())
                with col_k1:
                    start_km = st.number_input("From KM", min_value=1, max_value=max_km, value=1)
                with col_k2:
                    end_km = st.number_input("To KM", min_value=1, max_value=max_km, value=max_km)
                    
                new_pace = st.text_input("New Pace (mm:ss)", "06:00")
                nutrition_opts = st.multiselect("Nutrition", ["💧 Water", "🍯 Gel", "🍌 Food", "🧂 Salt"])
                new_notes = st.text_input("Notes (Optional)")
                
                submit_edits = st.form_submit_button("Apply to Plan", width="stretch")
                
                if submit_edits:
                    if re.match(r'^\d{1,2}:\d{2}$', new_pace):
                        mask = (df['KM'] >= start_km) & (df['KM'] <= end_km)
                        df.loc[mask, 'Pace (mm:ss)'] = new_pace
                        if new_notes: df.loc[mask, 'Notes'] = new_notes
                        df.loc[mask, ['💧 Water', '🍯 Gel', '🍌 Food', '🧂 Salt']] = False
                        for opt in nutrition_opts: df.loc[mask, opt] = True
                        st.session_state.race_plan = df
                        st.rerun()
                    else:
                        st.error("⚠️ Please format pace strictly as MM:SS (e.g., 08:30)")
            edited_df = df.copy()
        else:
            st.info("💻 **Desktop View:** Click directly into the table cells below to edit your pace and nutrition.")
            edited_df = st.data_editor(
                df, 
                column_config={
                    "KM": st.column_config.NumberColumn(disabled=True),
                    "Gain_m": st.column_config.NumberColumn("Gain (m)", disabled=True),
                    "Loss_m": st.column_config.NumberColumn("Loss (m)", disabled=True),
                    "Pace (mm:ss)": st.column_config.TextColumn("Pace (mm:ss)"),
                },
                hide_index=True, 
                width="stretch"
            )
            st.session_state.race_plan = edited_df

        # --- Reactive ETA Calculations ---
        edited_df['pace_sec'] = edited_df['Pace (mm:ss)'].apply(pace_to_seconds)
        edited_df['cum_sec'] = edited_df['pace_sec'].cumsum()
        edited_df['ETA'] = edited_df['cum_sec'].apply(seconds_to_eta)
        
        total_finish_time = edited_df['ETA'].iloc[-1] if not edited_df.empty else "00:00:00"
        eta_metric.metric("Estimated Finish Time", total_finish_time)
        
        cols = ['KM', 'Gain_m', 'Loss_m', 'Pace (mm:ss)', 'ETA', '💧 Water', '🍯 Gel', '🍌 Food', '🧂 Salt', 'Notes']
        final_display_df = edited_df[cols]
        
        if is_mobile:
            st.dataframe(
                final_display_df, hide_index=True, width="stretch",
                column_config={
                    "KM": st.column_config.NumberColumn("KM", width="small"),
                    "Gain_m": st.column_config.NumberColumn("🔺", width="small"),
                    "Loss_m": st.column_config.NumberColumn("🔻", width="small"),
                    "Pace (mm:ss)": st.column_config.TextColumn("Pace", width="small"),
                    "ETA": st.column_config.TextColumn("ETA", width="small"),
                    "💧 Water": st.column_config.CheckboxColumn("💧", width="small"),
                    "🍯 Gel": st.column_config.CheckboxColumn("🍯", width="small"),
                    "🍌 Food": st.column_config.CheckboxColumn("🍌", width="small"),
                    "🧂 Salt": st.column_config.CheckboxColumn("🧂", width="small"),
                }
            )

        # --- 📥 CSV EXPORT (Available to Everyone) ---
        st.divider()
        st.subheader("📥 Export Your Plan")
        st.info("Download your strategy to print out or use in Excel.")
        csv_data = final_display_df.to_csv(index=False).encode('utf-8')
        st.download_button(
            label="Download as CSV",
            data=csv_data,
            file_name="trail_race_strategy.csv",
            mime="text/csv",
            width="stretch"
        )

        # --- 💾 SUPABASE SAVE FEATURE (Logged In Users Only) ---
        if st.session_state.logged_in:
            st.divider()
            st.subheader("💾 Save to Cloud Profile")
            race_name = st.text_input("Give this race a name (e.g., UTMB 2026)")
            if st.button("Save Race Plan", width="stretch"):
                if race_name:
                    try:
                        # 🚀 UPDATED: Saving the extra analytical data
                        supabase.table('saved_races').insert({
                            'email': st.session_state.email,
                            'race_name': race_name,
                            'plan_json': final_display_df.to_json(orient='records'),
                            'distance_km': float(total_dist),
                            'elevation_gain_m': int(total_gain),
                            'finish_time': total_finish_time
                        }).execute()
                        st.success(f"'{race_name}' saved successfully to Supabase!")
                    except Exception as e:
                        st.error(f"Failed to save to cloud: {e}")
                else:
                    st.warning("Please enter a race name.")

# --- Saved Races & Profile Management ---
if st.session_state.logged_in:
    with saved_tab:
        st.subheader("Your Saved Races")
        res = supabase.table('saved_races').select('*').eq('email', st.session_state.email).order('id', desc=True).execute()
        saved_data = pd.DataFrame(res.data)
        
        if not saved_data.empty:
            for index, row in saved_data.iterrows():
                with st.expander(f"🏁 {row['race_name']}"):
                    
                    # 🚀 NEW: Display the analytical metadata safely
                    created_date = pd.to_datetime(row['created_at']).strftime('%Y-%m-%d') if pd.notnull(row.get('created_at')) else "Unknown Date"
                    
                    # The "or 0.0" protects against older NULL entries in the database
                    dist_km = float(row.get('distance_km') or 0.0)
                    gain_m = int(row.get('elevation_gain_m') or 0)
                    est_time = row.get('finish_time') or 'N/A'
                    
                    st.caption(f"**Saved:** {created_date} &nbsp;|&nbsp; **Distance:** {dist_km:.2f} km &nbsp;|&nbsp; **Gain:** {gain_m} m &nbsp;|&nbsp; **ETA:** {est_time}")
                    st.divider()

                    reconstructed_df = pd.read_json(io.StringIO(row['plan_json']), orient='records')
                    
                    if is_mobile:
                        st.dataframe(
                            reconstructed_df, hide_index=True, width="stretch",
                            column_config={
                                "KM": st.column_config.NumberColumn("KM", width="small"),
                                "Gain_m": st.column_config.NumberColumn("🔺", width="small"),
                                "Loss_m": st.column_config.NumberColumn("🔻", width="small"),
                                "Pace (mm:ss)": st.column_config.TextColumn("Pace", width="small"),
                                "ETA": st.column_config.TextColumn("ETA", width="small"),
                                "💧 Water": st.column_config.CheckboxColumn("💧", width="small"),
                                "🍯 Gel": st.column_config.CheckboxColumn("🍯", width="small"),
                                "🍌 Food": st.column_config.CheckboxColumn("🍌", width="small"),
                                "🧂 Salt": st.column_config.CheckboxColumn("🧂", width="small"),
                            }
                        )
                    else:
                        st.dataframe(reconstructed_df, hide_index=True, width="stretch")
                    
                    # 🚀 NEW: Side-by-Side Download and Delete Buttons
                    col_dl, col_del = st.columns(2)
                    with col_dl:
                        saved_csv = reconstructed_df.to_csv(index=False).encode('utf-8')
                        st.download_button(
                            label=f"📥 Download CSV",
                            data=saved_csv,
                            file_name=f"{row['race_name'].replace(' ', '_').lower()}.csv",
                            mime="text/csv",
                            key=f"download_{row['id']}",
                            width="stretch"
                        )
                    with col_del:
                        if st.button(f"🗑️ Delete Race", key=f"delete_{row['id']}", width="stretch"):
                            try:
                                supabase.table('saved_races').delete().eq('id', row['id']).execute()
                                st.rerun() # Refresh the page immediately
                            except Exception as e:
                                st.error(f"Failed to delete: {e}")
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

    if admin_tab:
        with admin_tab:
            st.subheader("App Performance Metrics")
            try:
                users_res = supabase.table('users').select('*', count='exact').execute()
                plans_res = supabase.table('saved_races').select('*', count='exact').execute()
                top_races_res = supabase.table('saved_races').select('email, race_name, distance_km, finish_time').order('id', desc=True).limit(5).execute()
                
                col1, col2 = st.columns(2)
                col1.metric("👥 Total Registered Users", users_res.count)
                col2.metric("💾 Total Saved Race Plans", plans_res.count)
                
                st.divider()
                st.write("**Recent App Activity (Last 5 Plans Created):**")
                # Showing the new analytics data in the admin table!
                st.dataframe(pd.DataFrame(top_races_res.data), hide_index=True, width="stretch")
            except Exception as e:
                st.error(f"Could not fetch metrics. {e}")
