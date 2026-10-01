import streamlit as st
import os
import json
import re
import smtplib
import requests
import time
import sqlite3
import random
import logging
import warnings
from email.message import EmailMessage
from pypdf import PdfReader
from apscheduler.schedulers.background import BackgroundScheduler

# --- Silence annoying library warnings ---
logging.getLogger("google").setLevel(logging.ERROR)
warnings.filterwarnings("ignore")

# --- 1. DATABASE SETUP (Upgraded with Memory Bank) ---
def init_db():
    with sqlite3.connect("hunters.db", check_same_thread=False) as conn:
        # Table for user profiles
        conn.execute(
            "CREATE TABLE IF NOT EXISTS subs ("
            "email TEXT PRIMARY KEY, "
            "role TEXT, "
            "loc TEXT, "
            "resume TEXT"
            ")"
        )
        # NEW: Table to track which jobs have been sent to which user
        conn.execute(
            "CREATE TABLE IF NOT EXISTS seen_jobs ("
            "email TEXT, "
            "job_hash TEXT, "
            "UNIQUE(email, job_hash)"
            ")"
        )

init_db()

# --- 2. THE CORE HUNTING ENGINE ---
def execute_hunt(target_role, search_location, user_email, user_resume, is_background=False):
    google_key = os.environ.get("GOOGLE_API_KEY", "").replace('"', '').replace("'", "").strip()
    rapid_key = os.environ.get("RAPIDAPI_KEY", "").replace('"', '').replace("'", "").strip()
    sender_email = os.environ.get("GMAIL_USER", "")
    app_password = os.environ.get("GMAIL_APP_PASS", "")

    if not all([google_key, rapid_key, sender_email, app_password]):
        if not is_background: st.error("⚠️ System Error: Missing API keys in terminal.")
        return

    limit = 3 if is_background else 5  
    
    try:
        # Scrape Google Jobs
        url = "https://jsearch.p.rapidapi.com/search-v2"
        querystring = {
            "query": f"{target_role} in {search_location}",
            "page": "1",
            "num_pages": "1",
            "date_posted": "month", 
            "country": "us",
            "language": "en"
        }
        headers = {
            "X-RapidAPI-Key": rapid_key,
            "X-RapidAPI-Host": "jsearch.p.rapidapi.com"
        }
        
        response = requests.get(url, headers=headers, params=querystring)
        data = response.json()
        
        if "data" not in data:
            if not is_background: st.error(f"🛑 API Error: {data}")
            return
            
        raw_data = data.get("data", [])
        jobs = raw_data.get("jobs", []) if isinstance(raw_data, dict) else raw_data
        valid_jobs = [j for j in jobs if isinstance(j, dict) and j.get("job_description")]
        
        # --- THE MEMORY BANK: Filter out jobs we've already sent ---
        with sqlite3.connect("hunters.db", check_same_thread=False) as conn:
            cursor = conn.execute("SELECT job_hash FROM seen_jobs WHERE email = ?", (user_email,))
            seen_hashes = {row[0] for row in cursor.fetchall()}

        fresh_jobs = []
        for job in valid_jobs:
            # Create a unique ID for the job based on its JSearch ID or Link
            job_hash = str(job.get('job_id')) or str(job.get('job_apply_link')) or (str(job.get('job_title')) + str(job.get('employer_name')))
            
            if job_hash not in seen_hashes:
                job['_tracking_hash'] = job_hash # Tag it so we can save it later
                fresh_jobs.append(job)

        # Stop if there are no fresh jobs left
        if not fresh_jobs:
            if not is_background: 
                st.warning("You have already seen all available jobs for this search! Broaden your location or check back tomorrow.")
            return

        # Pick random fresh jobs
        if len(fresh_jobs) > limit:
            top_jobs = random.sample(fresh_jobs, limit)
        else:
            top_jobs = fresh_jobs
            
        # Save these new jobs to the database so we NEVER send them to this user again
        with sqlite3.connect("hunters.db", check_same_thread=False) as conn:
            for job in top_jobs:
                conn.execute("INSERT OR IGNORE INTO seen_jobs (email, job_hash) VALUES (?, ?)", (user_email, job['_tracking_hash']))

        # Start formatting Plain Text Email
        email_text = f"🎯 YOUR JOB DOSSIER: {target_role.upper()}\n"
        email_text += f"Location: {search_location}\n"
        email_text += "========================================\n\n"

        if not is_background: my_bar = st.progress(0, text="AI Analyzing Jobs...")

        # Using the exact model Google told us to use
        model_roster = ['gemini-3.6-flash']

        for index, job in enumerate(top_jobs, 1):
            job_title = job.get('job_title', 'Unknown Title')
            job_company = job.get('employer_name', 'Unknown Company')
            job_url = job.get('job_apply_link') or job.get('job_google_link', '#')
            job_desc = job.get('job_description', '')
            
            if not is_background: my_bar.progress(index / len(top_jobs), text=f"Analyzing {job_title} at {job_company}...")
            
            prompt = (
                "Evaluate this candidate for THIS job. Return ONLY valid JSON with three keys:\n"
                "1. \"match_score\": integer from 0 to 100.\n"
                "2. \"missing_skills\": list of strings (skills they lack).\n"
                "3. \"verdict\": 1-sentence summary of why they should or shouldn't apply.\n\n"
                f"Candidate Resume:\n{user_resume}\n\nJob Description:\n{job_desc}"
            )

            success = False
            
            # THE RETRY ENGINE: Tries up to 3 times per job to beat rate limits
            for attempt in range(3):
                for ai_model in model_roster:
                    gemini_url = f"https://generativelanguage.googleapis.com/v1beta/models/{ai_model}:generateContent?key={google_key}"
                    payload = {
                        "contents": [{"parts": [{"text": prompt}]}]
                    }

                    try:
                        ai_req = requests.post(gemini_url, headers={'Content-Type': 'application/json'}, json=payload)
                        ai_data = ai_req.json()
                        
                        if "candidates" in ai_data:
                            raw_text = ai_data["candidates"][0]["content"]["parts"][0]["text"]
                            match = re.search(r'\{.*\}', raw_text, re.DOTALL)
                            
                            if match:
                                result = json.loads(match.group(0))
                                missing = ", ".join(result['missing_skills']) if result['missing_skills'] else "None! Perfect match 🌟"
                                
                                email_text += f"🏢 {job_title} @ {job_company}\n"
                                email_text += f"📊 Match Score: {result['match_score']}/100\n"
                                email_text += f"⚠️ Missing Skills: {missing}\n"
                                email_text += f"💡 AI Verdict: {result['verdict']}\n"
                                email_text += f"🔗 Apply Here: {job_url}\n"
                                email_text += "----------------------------------------\n\n"
                                success = True
                                break
                        else:
                            print(f"Attempt {attempt + 1} - API Error: {ai_data.get('error', {}).get('message', 'Unknown Error')}")
                    except Exception as e:
                        print(f"Attempt {attempt + 1} - REQUEST ERROR: {e}") 
                
                if success:
                    break 
                    
                print("Cooling down for 10 seconds to bypass rate limit...")
                time.sleep(10)

            if not success:
                email_text += f"🏢 {job_title} @ {job_company}\n"
                email_text += "⚠️ AI Analysis skipped (Rate limit cooldown)\n"
                email_text += f"🔗 Apply Here: {job_url}\n"
                email_text += "----------------------------------------\n\n"
                
            time.sleep(15) 

        if not is_background: my_bar.empty()

        # Dispatch Email
        msg = EmailMessage()
        msg['Subject'] = f"🎯 Job Hunter: {len(top_jobs)} New {target_role} Roles"
        msg['From'] = sender_email
        msg['To'] = user_email 
        msg.set_content(email_text)

        with smtplib.SMTP_SSL('smtp.gmail.com', 465) as smtp:
            smtp.login(sender_email, app_password)
            smtp.send_message(msg)
        
        if not is_background: 
            st.success(f"✅ BOOM! Your dossier has been successfully delivered to {user_email}!")
            st.balloons()
            
    except Exception as e:
        if not is_background: st.error(f"An error occurred: {str(e)}")


# --- 3. BACKGROUND SCHEDULER LOGIC ---
def background_job_loop():
    with sqlite3.connect("hunters.db", check_same_thread=False) as conn:
        users = conn.execute("SELECT email, role, loc, resume FROM subs").fetchall()
    
    for email, role, loc, resume in users:
        execute_hunt(role, loc, email, resume, is_background=True)

if "scheduler" not in st.session_state:
    scheduler = BackgroundScheduler()
    scheduler.add_job(background_job_loop, 'interval', hours=24)
    scheduler.start()
    st.session_state.scheduler = scheduler


# --- 4. STREAMLIT UI ---
st.set_page_config(page_title="Job Hunter Agent", page_icon="🎯", layout="centered")
st.title("🎯 Job Hunter: AI Career Agent")
st.write("Upload your resume, set your target role, get your **immediate** dossier, and enroll in daily morning updates.")

target_role = st.text_input("What specific role are you looking for? (e.g., Junior AI Engineer)")
search_location = st.text_input("Location? (e.g., Remote, New York, London)")
user_email = st.text_input("Where should we send your dossier? (Your Email)")
uploaded_file = st.file_uploader("Upload your Resume (PDF)", type=["pdf"])
enable_daily = st.checkbox("🔔 Enroll me in the Daily Automated Morning Job Dossier", value=True)

if st.button("Hunt & Subscribe", type="primary"):
    if not all([target_role, search_location, user_email, uploaded_file]):
        st.error("⚠️ Please fill out all fields and upload your resume.")
    else:
        reader = PdfReader(uploaded_file)
        user_resume = "".join([page.extract_text() + "\n" for page in reader.pages])

        if enable_daily:
            with sqlite3.connect("hunters.db", check_same_thread=False) as conn:
                conn.execute(
                    "INSERT OR REPLACE INTO subs (email, role, loc, resume) "
                    "VALUES (?, ?, ?, ?)", 
                    (user_email, target_role, search_location, user_resume)
                )

        with st.spinner(f"Scraping Google Jobs and running deep AI analysis..."):
            execute_hunt(target_role, search_location, user_email, user_resume, is_background=False)