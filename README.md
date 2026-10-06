# Srahi prototype kit — 1 month, 10 seniors, ₹5,000

## What this is
A one-button voice companion that runs in the phone browser (installs like an app).
Flow: senior taps the big red button and speaks → Groq Whisper (STT) → Llama on Groq
(LLM, with personal memory = lightweight RAG) → Edge-TTS (Hindi/Indian-English voice) → spoken reply.
Every interaction is logged to SQLite. A `report.py` turns that into your investor CSV.
The app also delivers a proactive morning briefing at each senior's set time — the habit driver.

## Setup (one evening)
1. Get a free Groq API key: https://console.groq.com  (no card, free tier is enough for 10 seniors)
2. `pip install -r requirements.txt`
3. `set GROQ_API_KEY=...` (Windows) or `export GROQ_API_KEY=...` (Mac/Linux)
4. `uvicorn app:app --host 0.0.0.0 --port 8000`
5. Make it reachable from the seniors' phones (pick one):
   - Easiest: Cloudflare quick tunnel — install `cloudflared`, run
     `cloudflared tunnel --url http://localhost:8000`, open the https URL it prints
     (URL changes on restart, fine for a month).
   - Best: free Oracle Cloud VM (always free) — run there and the URL stays fixed.
6. On each senior's phone: open `https://<your-url>/?uid=kamala`, Chrome menu →
   "Add to Home Screen". It now launches fullscreen like a real app.

## Per-senior onboarding (15 min each)
1. Copy `seniors/amma.json` → `seniors/<their-name>.json`. Fill name, language
   (hi / en / hi-m for male Hindi voice), city, interests, briefing_time (e.g. "07:30").
2. Add 3-5 starter facts (family names, routine, health notes) — this is the memory
   that makes replies feel personal from day 1.
3. On their phone, open the URL with `?uid=<their-name>`, grant mic, add to home screen.
4. Put the phone in a neck pouch or desk stand. Speaker on, volume up.
5. Do the first conversation WITH them: "Srahi, namaste" — show the tap-tap pattern.
   Record a 2-min video of this (with permission). This is your investor "before → after" proof.

## Running the month
- Week 1: visit every senior once. Call each twice. Fix the top 3 complaints fast
  (usually: too slow → check Wi-Fi; can't hear → volume/earphones; wrong language → profile).
- Every talk is logged (time, transcript, latency, intent, errors). Check
  `http://<your-url>/summary?days=7` weekly to see active days per senior.
- Weeks 2–4: let it run. Weekly calls. Record testimonials on the final visit.

## Investor report (day 30)
`python report.py 30` → `srahi_report_30d.csv`: per senior — voice sessions, active days,
avg latency, companion vs. task chats, errors. Pair with: before/after videos, 3 written
testimonials, and the willingness-to-pay question (ask each senior: "₹150/month — yes or no?").

## Notes
- APIs cost ₹0 this month (Groq free tier; Edge-TTS free; weather via wttr.in free).
- Later integrations (Uber/Zomato/etc.) slot into the same /talk pipeline as tool calls —
  the intent detector in app.py already tags call/message/music/news/medicine/grocery requests,
  which is your demand map for which API to integrate first.
