# 🎯 Job Hunter: AI-Powered Career Agent

Job Hunter is an autonomous job search agent that works while you sleep. It scrapes live job boards, cross-references requirements against your resume using Google's Gemini AI, and delivers a personalized dossier of high-match roles directly to your inbox every morning.

## 🚀 Core Features
* **Automated Scraping:** Pulls live jobs via the RapidAPI JSearch engine.
* **Deep AI Analysis:** Uses Gemini 3.6 Flash to read your PDF resume, score your match percentage (0-100), and identify missing skills for each specific role.
* **Smart Memory Bank:** Tracks previously seen jobs via SQLite so you never get emailed the same listing twice.
* **Daily Dispatch:** Uses APScheduler to automatically email your dossier every 24 hours.

## 🛠️ Tech Stack
* **Python & Streamlit** (Frontend/Backend)
* **Google Gemini AI** (LLM Analysis)
* **SQLite** (Memory Bank / User State)
* **RapidAPI** (Data Aggregation)
