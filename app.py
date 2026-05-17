import os
import json
import requests
from fastapi import FastAPI, Request, BackgroundTasks, Header, HTTPException
from dotenv import load_dotenv
from ddgs import DDGS
from nacl.signing import VerifyKey
from nacl.exceptions import BadSignatureError
import asyncio

load_dotenv()

app = FastAPI(title="Universal Networking CRM")

# Optional environment variables
TELEGRAM_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
DISCORD_APP_ID = os.getenv("DISCORD_APP_ID")
DISCORD_PUBLIC_KEY = os.getenv("DISCORD_PUBLIC_KEY")

# --- PLATFORM ROUTER ---
class ReplyContext:
    """Universal messaging router. Knows how to reply to both TG and Discord."""
    def __init__(self, platform: str, target_id: str):
        self.platform = platform
        self.target_id = target_id # Chat ID for TG, Interaction Token for Discord

    def send(self, text: str):
        if self.platform == "telegram" and TELEGRAM_TOKEN:
            url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
            requests.post(url, json={"chat_id": self.target_id, "text": text, "parse_mode": "HTML"})
            
        elif self.platform == "discord" and DISCORD_APP_ID:
            # Discord requires a PATCH to update the deferred interaction response
            url = f"https://discord.com/api/v10/webhooks/{DISCORD_APP_ID}/{self.target_id}/messages/@original"
            
            # Convert basic HTML bold/italics to Discord Markdown
            formatted_text = text.replace("<b>", "**").replace("</b>", "**").replace("<i>", "*").replace("</i>", "*")
            requests.patch(url, json={"content": formatted_text})

# --- CORE BRAIN (Unchanged, just uses ReplyContext now) ---
# --- 1. PRIMARY BRAIN: GEMINI ---
def parse_with_gemini(raw_text: str):
    url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent?key={os.getenv('GEMINI_API_KEY')}"
    prompt = f"""
    You are an AI networking CRM. Analyze this note: "{raw_text}"
    
    Return a JSON object using EXACTLY this structure and these exact keys:
    {{
        "name": "",
        "role": "",
        "company": "",
        "industry": "",
        "date_met": "",
        "location_met": "",
        "context_summary": "professionally rewritten summary",
        "action": "extracted follow-up action, or empty string if none"
    }}
    Leave values as empty strings "" if missing.
    """
    payload = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {"responseMimeType": "application/json"}
    }
    
    try:
        response = requests.post(url, headers={'Content-Type': 'application/json'}, json=payload)
        if response.status_code == 429:
            return "RATE_LIMIT"
        response.raise_for_status()
        return json.loads(response.json()['candidates'][0]['content']['parts'][0]['text'])
    except Exception as e:
        print(f"❌ Gemini error: {e}")
        return None

# --- 2. FALLBACK BRAIN: OPENAI ---
def parse_with_openai(raw_text: str):
    prompt = """
    You are an AI networking CRM. Analyze the user's note.
    
    Return a JSON object using EXACTLY this structure and these exact keys:
    {
        "name": "",
        "role": "",
        "company": "",
        "industry": "",
        "date_met": "",
        "location_met": "",
        "context_summary": "professionally rewritten summary",
        "action": "extracted follow-up action, or empty string if none"
    }
    Leave values as empty strings "" if missing.
    """
    try:
        client = openai.OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
        response = client.chat.completions.create(
            model="gpt-4o-mini",
            response_format={ "type": "json_object" },
            messages=[
                {"role": "system", "content": prompt},
                {"role": "user", "content": raw_text}
            ]
        )
        return json.loads(response.choices[0].message.content)
    except Exception as e:
        print(f"❌ OpenAI error: {e}")
        return None

# --- 3. THE FAILOVER ROUTER ---
def parse_note_with_fallback(raw_text: str):
    """Attempts Gemini first, seamlessly switches to OpenAI if it fails."""
    print("🔄 Routing to Gemini (Primary)...")
    result = parse_with_gemini(raw_text)
    
    if result == "RATE_LIMIT" or result is None:
        print("⚠️ Gemini unavailable or rate-limited. Falling back to OpenAI...")
        result = parse_with_openai(raw_text)
        
    return result

def find_linkedin(name: str, company: str, role: str):
    """Strict background search using standard requests to avoid async event loop crashes."""
    if not name or not company:
        return ""
        
    query = f'"{name}" "{company}" {role} site:linkedin.com/in/'
    print(f"🕵️‍♂️ Searching DDGS with exact query: {query}")
    
    # We use DuckDuckGo's HTML-only endpoint which requires zero async event loops
    url = "https://html.duckduckgo.com/html/"
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    }
    payload = {"q": query}
    
    try:
        # Simple, stable, synchronous POST request with a 10-second timeout
        response = requests.post(url, headers=headers, data=payload, timeout=10)
        
        if response.status_code == 200:
            html_text = response.text
            
            # Simple string parsing to look for raw LinkedIn URLs in the HTML response
            import re
            links = re.findall(r'href="([^"]+)"', html_text)
            
            for link in links:
                # Clean up DuckDuckGo's internal redirect styling if present
                if "linkedin.com/in/" in link:
                    # Sometimes DDG wraps URLs like /l/?kh=-1&uddg=https://linkedin.com/in/abc
                    if "uddg=" in link:
                        link = link.split("uddg=")[1].split("&")[0]
                        import urllib.parse
                        link = urllib.parse.unquote(link)
                        
                    print(f"✅ Verified LinkedIn: {link}")
                    return link
                    
            print("⚠️ No LinkedIn profile found in search results.")
        else:
            print(f"⚠️ Search engine returned status code: {response.status_code}")
            
    except Exception as e:
        print(f"⚠️ Web search error: {e}")
        
    return ""

def push_to_airtable(data: dict, linkedin_url: str):
    url = f"https://api.airtable.com/v0/{os.getenv('AIRTABLE_BASE_ID')}/Contacts"
    headers = {"Authorization": f"Bearer {os.getenv('AIRTABLE_PAT')}", "Content-Type": "application/json"}
    fields = {"Name": data.get("name", "Unknown")}
    
    def add_val(key, val):
        if val and str(val).strip() and str(val).strip() != "Unspecified": fields[key] = str(val).strip()

    add_val("Current Role", data.get("role"))
    add_val("Company", data.get("company"))
    add_val("Industry", data.get("industry"))
    add_val("Date Met", data.get("date_met"))
    add_val("Location Met", data.get("location_met"))
    add_val("Context Summary", data.get("context_summary"))
    add_val("Action", data.get("action"))
    if linkedin_url: fields["LinkedIn"] = linkedin_url

    try:
        requests.post(url, headers=headers, json={"fields": fields}).raise_for_status()
        return True
    except Exception: return False

def process_and_enrich(raw_text: str, ctx: ReplyContext):
    parsed = parse_note_with_fallback(raw_text)
    
    if not parsed:
        return ctx.send("❌ Both AI engines failed to understand that note. Try rephrasing?")
        
    if not parsed:
        return ctx.send("❌ I couldn't understand that note. Try rephrasing?")
    
    linkedin_url = find_linkedin(parsed.get("name"), parsed.get("company"), parsed.get("role"))
    if push_to_airtable(parsed, linkedin_url):
        msg = f"✅ **Saved {parsed.get('name')} to CRM!**"
        if parsed.get("action"): msg += f"\n🎯 **Action:** {parsed.get('action')}"
        ctx.send(msg)
    else:
        ctx.send("❌ Failed to save to Airtable.")

def search_airtable(query: str, ctx: ReplyContext):
    url = f"https://api.airtable.com/v0/{os.getenv('AIRTABLE_BASE_ID')}/Contacts"
    headers = {"Authorization": f"Bearer {os.getenv('AIRTABLE_PAT')}"}
    formula = f"SEARCH(LOWER('{query}'), LOWER(CONCATENATE({{Name}}, ' ', {{Current Role}}, ' ', {{Company}}, ' ', {{Industry}}, ' ', {{Location Met}}, ' ', {{Context Summary}})))"
    
    try:
        records = requests.get(url, headers=headers, params={"filterByFormula": formula}).json().get("records", [])
        if not records: return ctx.send(f"🤷‍♂️ No results found for '{query}'.")

        if len(records) == 1:
            c = records[0]["fields"]
            ctx.send(f"👤 **{c.get('Name', 'Unknown')}** — *{c.get('Current Role', 'Unknown')}*\n🏢 **{c.get('Company', 'No Company')}**\n📅 **Action:** {c.get('Action', 'None')}\n🔗 {c.get('LinkedIn', 'No LinkedIn')}\n📝 **Notes:** {c.get('Context Summary', '')}")
        else:
            reply = f"🎯 Found {len(records)} matches for '**{query}**':\n\n"
            for r in records[:5]:
                reply += f"🔹 **{r['fields'].get('Name', 'Unknown')}** - {r['fields'].get('Company', 'No Company')}\n"
            ctx.send(reply)
    except Exception: ctx.send("⚠️ Error connecting to Airtable.")

# --- TELEGRAM ENDPOINT ---
@app.post("/webhook")
async def telegram_webhook(request: Request, background_tasks: BackgroundTasks):
    payload = await request.json()
    if "message" in payload and "text" in payload["message"]:
        text = payload["message"]["text"]
        ctx = ReplyContext(platform="telegram", target_id=payload["message"]["chat"]["id"])
        
        if text.startswith("/find"):
            background_tasks.add_task(search_airtable, text.replace("/find", "").strip(), ctx)
        else:
            ctx.send("⏳ Parsing notes...")
            background_tasks.add_task(process_and_enrich, text, ctx)
    return {"status": "queued"}

# --- DISCORD ENDPOINT ---
@app.post("/discord-webhook")
async def discord_webhook(
    request: Request, 
    background_tasks: BackgroundTasks,
    x_signature_ed25519: str = Header(None),
    x_signature_timestamp: str = Header(None)
):
    if not DISCORD_PUBLIC_KEY:
        raise HTTPException(status_code=400, detail="Discord not configured")

    # 1. Cryptographic Security Check
    body = await request.body()
    verify_key = VerifyKey(bytes.fromhex(DISCORD_PUBLIC_KEY))
    try:
        verify_key.verify(f"{x_signature_timestamp}{body.decode('utf-8')}".encode(), bytes.fromhex(x_signature_ed25519))
    except BadSignatureError:
        raise HTTPException(status_code=401, detail="Invalid request signature")

    payload = json.loads(body)
    
    # 2. Handle Discord Verification Ping
    if payload.get("type") == 1:
        return {"type": 1}

    # 3. Handle Slash Commands
    if payload.get("type") == 2:
        command_name = payload["data"]["name"]
        interaction_token = payload["token"]
        
        # Get the text the user typed
        options = payload["data"].get("options", [])
        user_input = options[0]["value"] if options else ""

        ctx = ReplyContext(platform="discord", target_id=interaction_token)

        if command_name == "save":
            background_tasks.add_task(process_and_enrich, user_input, ctx)
        elif command_name == "find":
            background_tasks.add_task(search_airtable, user_input, ctx)

        # Discord requires an immediate response < 3 seconds. Type 5 means "Bot is thinking..."
        return {"type": 5}