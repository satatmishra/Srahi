"""Srahi prototype v2 — voice companion + human escalation.
Run:  uvicorn app:app --host 0.0.0.0 --port 8000
"""
import os, json, sqlite3, asyncio, base64, time, datetime, threading, urllib.request
from pathlib import Path

from fastapi import FastAPI, UploadFile, Form, Query
from fastapi.responses import JSONResponse, FileResponse
from groq import Groq
import edge_tts

KEY = os.environ["GROQ_API_KEY"]
client = Groq(api_key=KEY)
DATA = Path("seniors")
DB = "srahi.db"

STT_MODEL = "whisper-large-v3"
LLM_MODEL = "llama-3.3-70b-versatile"
VOICES = {"hi": "hi-IN-SwaraNeural", "en": "en-IN-NeerjaNeural",
          "hi-m": "hi-IN-MadhurNeural", "en-m": "en-IN-PrabhatMNeural"}

TG_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")   # optional: family alerts
TG_CHAT  = os.environ.get("TELEGRAM_CHAT_ID", "")
FOUNDER_NUMBER = os.environ.get("FOUNDER_NUMBER", "")  # you, as the human fallback

SYSTEM_PROMPT = """You are Srahi (साथी+राही), a warm, patient voice companion for an elderly person in India. You are NOT a chatbot on a screen — you are a friend they talk to by voice.
Rules:
- Reply in SHORT spoken sentences (1-3 sentences). Never use lists, markdown, or emojis.
- Speak naturally in the language the person uses. If they mix Hindi and English (Hinglish), you mix freely too. Match their language every turn.
- Be warm, unhurried, respectful. Use their name sometimes. Ask gentle follow-up questions.
- Never mention you are an AI, an app, or technology unless directly asked. Then answer simply.
- If you genuinely do not know something (train timing, a medical question, a detail about their family), say warmly that you will note it down and their family will help — NEVER guess or invent an answer. Saying you don't know is good.
- If they ask for a real person, say you are connecting them right away.
- If they seem confused or distressed, be extra gentle and suggest talking to family.
- Keep answers simple — imagine speaking to a grandparent with mild hearing difficulty."""

app = FastAPI()
_db = sqlite3.connect(DB, check_same_thread=False)
_db.execute("""CREATE TABLE IF NOT EXISTS events(
 id INTEGER PRIMARY KEY, ts TEXT, uid TEXT, kind TEXT, stt TEXT, reply TEXT,
 latency_ms INTEGER, intent TEXT, error TEXT)""")
_db.execute("""CREATE TABLE IF NOT EXISTS pending(
 id INTEGER PRIMARY KEY, uid TEXT, created TEXT, audio BLOB, text TEXT)""")
FAILS = {}   # uid -> consecutive STT failures

def log(kind, uid, stt="", reply="", lat=None, intent="", error=""):
    _db.execute("INSERT INTO events VALUES(NULL,?,?,?,?,?,?,?,?)",
        (datetime.datetime.now().isoformat(timespec="seconds"), uid, kind,
         stt, reply, lat, intent, error))
    _db.commit()

def telegram(msg):
    if not (TG_TOKEN and TG_CHAT): return
    try:
        url = f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage?chat_id={TG_CHAT}&text={urllib.parse.quote(msg)}"
        urllib.request.urlopen(url, timeout=5)
    except Exception:
        pass
import urllib.parse

EMERGENCY = ["chest pain","heart attack","saans","सांस","दिल","हार्ट","गिर गया","गिर गई",
             "gir gay","fell down","चक्कर","emergency"," hospital","हॉस्पिटल","bleeding","खून"]
HUMAN_ASKS = ["real person","asli insaan","असली इंसान","insaan se baat","human",
              "kisi se baat karao","किसी से बात","founder","manager"]

def detect_intent(t):
    tl = " " + t.lower() + " "
    for name, kws in [("call",("call ","phone ","फोन ","कॉल ")),("message",("message ","whatsapp","संदेश ")),
        ("music",("song","gaana","गाना","music","भजन")),("news",("news","खबर","samachar")),
        ("medicine",("medicine","dawai","दवा","tablet")),("grocery",("grocery","sabzi","सब्जी","ration"))]:
        if any(k in tl for k in kws): return name
    return "chat"

def load_profile(uid):
    p = DATA / f"{uid}.json"
    prof = json.loads(p.read_text()) if p.exists() else {"name": uid, "language": "hi", "facts": []}
    prof.setdefault("facts", [])
    return prof

def save_profile(uid, prof):
    (DATA / f"{uid}.json").write_text(json.dumps(prof, ensure_ascii=False, indent=2))

def history(uid, n=8):
    rows = _db.execute("SELECT stt, reply FROM events WHERE uid=? AND kind='talk' AND error=''                        ORDER BY id DESC LIMIT ?", (uid, n)).fetchall()
    return ([{"role": "user", "content": r[0]} for r in reversed(rows)] +
            [{"role": "assistant", "content": r[1]} for r in reversed(rows)])

def transcribe(audio_bytes, fname):
    r = client.audio.transcriptions.create(model=STT_MODEL,
            file=(fname, audio_bytes), response_format="json")
    return r.text.strip()

def chat(uid, text):
    prof = load_profile(uid)
    facts = "\n".join(f"- {f}" for f in prof["facts"][-30:])
    msgs = [{"role": "system", "content": SYSTEM_PROMPT +
             f"\n\nPerson you are talking to: {prof.get('name','')}." +
             (f"\nWhat you know about them:\n{facts}" if facts else "")},
            *history(uid), {"role": "user", "content": text}]
    r = client.chat.completions.create(model=LLM_MODEL, messages=msgs,
                                       max_tokens=160, temperature=0.7)
    return r.choices[0].message.content.strip(), prof

async def tts_bytes(text, lang):
    buf = bytearray()
    async for chunk in edge_tts.Communicate(text, VOICES.get(lang, VOICES["hi"])).stream():
        if chunk["type"] == "audio":
            buf.extend(chunk["data"])
    return bytes(buf)

def remember(uid, user_text):
    try:
        prof = load_profile(uid)
        r = client.chat.completions.create(model="llama-3.1-8b-instant", temperature=0,
            max_tokens=60, messages=[{"role": "system", "content":
              "Extract ONE new fact about the elderly person (family names, routine, health, likes). Reply with ONLY the fact, or the word NONE."},
              {"role": "user", "content": f"Known facts: {prof['facts']}\nThey said: {user_text}"}])
        fact = r.choices[0].message.content.strip().rstrip(".")
        if fact and fact.upper() != "NONE" and fact not in prof["facts"]:
            prof["facts"].append(fact); save_profile(uid, prof)
    except Exception:
        pass

async def build_response(uid, reply_text, action=None, kind="talk", intent="", lat=None):
    prof = load_profile(uid)
    audio = await tts_bytes(reply_text, prof.get("language", "hi"))
    log(kind, uid, reply=reply_text, lat=lat, intent=intent)
    out = {"text": reply_text, "audio": base64.b64encode(audio).decode()}
    if action: out["action"] = action
    return JSONResponse(out)

@app.post("/talk")
async def talk(uid: str = Form(...), audio: UploadFile = Form(...)):
    t0 = time.time()
    prof = load_profile(uid)
    try:
        raw = await audio.read()
        text = transcribe(raw, audio.filename or "v.webm")
    except Exception as e:
        log("talk", uid, error=str(e)[:200]); text = ""

    # --- escalation layer: couldn't understand 3 times in a row ---
    if not text:
        FAILS[uid] = FAILS.get(uid, 0) + 1
        if FAILS[uid] >= 3:
            FAILS[uid] = 0
            telegram(f"⚠️ Srahi could not understand {prof.get('name',uid)} 3 times in a row. Please check on them.")
            return await build_response(uid,
                "Maaf kijiye, mujhe samajh nahi aa raha. Main aapke parivar ko bata rahi hoon. Woh aapko phone karenge.",
                action={"type": "alert"}, kind="escalation")
        return await build_response(uid, "Samajh nahi aaya, phir se boliye?")
    FAILS[uid] = 0
    tl = text.lower()

    # --- escalation layer: medical emergency ---
    if any(k in tl for k in EMERGENCY):
        fam = prof.get("family_number", "")
        telegram(f"🚨 EMERGENCY — {prof.get('name',uid)} said: \"{text}\". Call them now: {fam or 'no number on profile'}")
        log("emergency", uid, stt=text)
        return await build_response(uid,
            f"{prof.get('name','')}, aap chinta mat kijiye. Main {prof.get('family_name','aapke parivar')} ko abhi bula rahi hoon. Woh turant phone karenge.",
            action={"type": "dial", "number": fam or FOUNDER_NUMBER} if (fam or FOUNDER_NUMBER) else {"type": "alert"},
            kind="emergency")

    # --- escalation layer: senior asks for a human ---
    if any(k in tl for k in HUMAN_ASKS):
        log("human_request", uid, stt=text)
        return await build_response(uid,
            "Bilkul, abhi aapko kisi se baat karati hoon. Please wait.",
            action={"type": "dial", "number": FOUNDER_NUMBER} if FOUNDER_NUMBER else {"type": "alert"},
            kind="human_request")

    # --- normal conversation ---
    try:
        reply, _ = chat(uid, text)
        lat = int((time.time() - t0) * 1000)
        intent = detect_intent(text)
        log("talk", uid, text, reply, lat, intent)
        threading.Thread(target=remember, args=(uid, text), daemon=True).start()
        audio_out = await tts_bytes(reply, prof.get("language", "hi"))
        return JSONResponse({"text": reply, "audio": base64.b64encode(audio_out).decode(),
                             "latency_ms": lat})
    except Exception as e:
        log("talk", uid, stt=text, error=str(e)[:200])
        return await build_response(uid, "Thodi dikkat ho gayi. Ek minute ruk kar phir boliye?")

def weather_line(city):
    if not city: return ""
    try:
        w = json.load(urllib.request.urlopen(f"https://wttr.in/{city}?format=j1", timeout=5))
        d = w["current_condition"][0]
        hi, lo = w["weather"][0]["maxtempC"], w["weather"][0]["mintempC"]
        return f"Aaj ka mausam: {d['lang_hi'][0]['value'] if d.get('lang_hi') else d['weatherDesc'][0]['value']}, taapman {lo} se {hi} degree."
    except Exception:
        return ""

async def make_briefing(uid):
    prof = load_profile(uid)
    lang = prof.get("language", "hi")
    greet = f"Suprabhat {prof.get('name','')}! Main Srahi hoon." if lang == "hi" else f"Good morning {prof.get('name','')}! Srahi here."
    interests = ", ".join(prof.get("interests", []))
    line = f"{greet} {weather_line(prof.get('city',''))} Aaj kya plan hai? {'Aapke pasand ke baare mein baat karein: ' + interests if interests else 'Chai ho gayi?'}"
    audio = await tts_bytes(line, lang)
    _db.execute("INSERT INTO pending VALUES(NULL,?,?,?,?,?)",
        (uid, datetime.datetime.now().isoformat(timespec="seconds"), audio, line))
    _db.commit()
    log("briefing", uid, reply=line)

@app.get("/pending")
async def pending(uid: str = Query(...)):
    rows = _db.execute("SELECT id, audio, text FROM pending WHERE uid=?", (uid,)).fetchall()
    for rid, a, t in rows: _db.execute("DELETE FROM pending WHERE id=?", (rid,))
    _db.commit()
    return {"messages": [{"audio": base64.b64encode(a).decode(), "text": t} for a, t in
                         [(r[1], r[2]) for r in rows]]}

@app.get("/summary")
async def summary(days: int = 7):
    rows = _db.execute("""SELECT uid, COUNT(*), COUNT(DISTINCT substr(ts,1,10)),
        ROUND(AVG(latency_ms)) FROM events
        WHERE kind='talk' AND error='' AND ts >= datetime('now', ?)
        GROUP BY uid""", (f"-{days} days",)).fetchall()
    esc = _db.execute("""SELECT uid, kind, COUNT(*) FROM events
        WHERE kind IN ('emergency','human_request','escalation') GROUP BY uid, kind""").fetchall()
    return {"per_senior": [{"uid": r[0], "sessions": r[1], "active_days": r[2],
            "avg_latency_ms": r[3]} for r in rows],
            "escalations": [{"uid": e[0], "kind": e[1], "count": e[2]} for e in esc]}

@app.get("/")
async def home():
    return FileResponse("static/index.html")

@app.get("/admin/download")
async def download():
    """Open this URL in a browser anytime to save all pilot data (srahi.db)."""
    return FileResponse(DB, media_type="application/octet-stream",
                        filename="srahi_backup.db")

@app.on_event("startup")
async def scheduler():
    async def loop():
        while True:
            now = datetime.datetime.now()
            for f in DATA.glob("*.json"):
                prof = json.loads(f.read_text())
                if now.strftime("%H:%M") == prof.get("briefing_time", "08:00") \
                   and prof.get("last_briefing") != now.strftime("%Y-%m-%d"):
                    prof["last_briefing"] = now.strftime("%Y-%m-%d")
                    f.write_text(json.dumps(prof, ensure_ascii=False, indent=2))
                    try: await make_briefing(f.stem)
                    except Exception: pass
            await asyncio.sleep(30)
    asyncio.create_task(loop())

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("PORT", 8000)))
