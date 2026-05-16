import os
import json
import requests
from fastapi import FastAPI, Request, BackgroundTasks
from dotenv import load_dotenv
from ddgs import DDGS

load_dotenv()

app = FastAPI(title="Networking-CRM-Bot")

REQUIRED_ENV_VARS = [
    "TELEGRAM_BOT_TOKEN", 
    "GEMINI_API_KEY", 
    "AIRTABLE_PAT", 
    "AIRTABLE_BASE_ID"
]

missing_vars = [var for var in REQUIRED_ENV_VARS if not os.getenv(var)]
if missing_vars:
    raise RuntimeError(f"Missing required environment variables in .env: {', '.join(missing_vars)}")

TELEGRAM_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")

def send_telegram_message(chat_id: int, text: str):
    """Sends a message back to the user in Telegram."""
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {"chat_id": chat_id, "text": text, "parse_mode": "HTML"}
    try:
        requests.post(url, json=payload)
    except Exception as e:
        print(f"Failed to send Telegram message: {e}")

def parse_with_gemini(raw_text: str):
    """Uses Gemini to extract data, polish the summary, and identify follow-up actions."""
    print(f"🧠 Asking Gemini to parse: '{raw_text}'...")
    url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent?key={os.getenv('GEMINI_API_KEY')}"
    
    prompt = f"""
    You are an AI assistant for a networking CRM. Analyze the following unstructured note: "{raw_text}"
    
    Perform these tasks:
    1. Extract the person's first and last name.
    2. Extract their company/organization.
    3. Extract the location where we met.
    4. Rewrite and polish the context of our interaction into a clear, professional summary. Fix any typos or bad grammar.
    5. Identify any follow-up task or 'action' required (e.g., 'Email next week', 'Send pitch deck'). If no action is needed, leave it empty.
    
    Return ONLY a valid JSON object with exactly these keys: "name", "company", "location_met", "context_summary", "action".
    If a value isn't found, leave it as an empty string. Do not use markdown backticks.
    """
    
    payload = {"contents": [{"parts": [{"text": prompt}]}]}
    try:
        response = requests.post(url, headers={'Content-Type': 'application/json'}, json=payload)
        response.raise_for_status()
        result_text = response.json()['candidates'][0]['content']['parts'][0]['text']
        clean_json_string = result_text.replace("```json", "").replace("```", "").strip()
        return json.loads(clean_json_string)
    except Exception as e:
        print(f"❌ Gemini parsing failed: {e}")
        return None

def enrich_profile(name: str, company: str):
    """Uses a headless web search to find the person's LinkedIn profile for free."""
    print(f"🚀 Searching the web for {name} at {company}...")
    query = f'{name} {company} site:linkedin.com/in/'
    
    try:
        results = list(DDGS().text(query, max_results=1))
        if results:
            linkedin_url = results[0].get("href", "")
            print(f"✅ Found LinkedIn: {linkedin_url}")
            return {"title": "See Context Summary", "email": "N/A (Web Search)", "linkedin": linkedin_url}
    except Exception as e:
        print(f"⚠️ Web search failed: {e}")
        
    return {"title": "Unknown Role", "email": "No Email Found", "linkedin": "No LinkedIn Found"}

def push_to_airtable(name: str, context: str, location: str, action: str, info: dict):
    """Pushes a clean structured record into Airtable."""
    print(f"📝 Pushing {name} to Airtable...")
    base_id = os.getenv("AIRTABLE_BASE_ID")
    url = f"https://api.airtable.com/v0/{base_id}/Contacts"
    
    headers = {
        "Authorization": f"Bearer {os.getenv('AIRTABLE_PAT')}",
        "Content-Type": "application/json"
    }
    
    payload = {
        "fields": {
            "Name": name,
            "Current Role": info["title"],
            "Context Summary": context,
            "Location Met": location,
            "Action": action,
            "Email": info["email"] if "No" not in info["email"] else "",
            "LinkedIn": info["linkedin"] if "http" in info["linkedin"] else ""
        }
    }
    
    try:
        response = requests.post(url, headers=headers, json=payload)
        response.raise_for_status()
        return True
    except Exception as e:
        print(f"❌ Failed pushing to Airtable: {e}")
        return False

def search_airtable(query: str, chat_id: int):
    """Searches Airtable by partial name and handles multiple results."""
    send_telegram_message(chat_id, f"🔍 Searching CRM for <b>{query}</b>...")
    
    base_id = os.getenv("AIRTABLE_BASE_ID")
    url = f"https://api.airtable.com/v0/{base_id}/Contacts"
    headers = {"Authorization": f"Bearer {os.getenv('AIRTABLE_PAT')}"}
    
    params = {"filterByFormula": f"SEARCH(LOWER('{query}'), LOWER({{Name}}))"}
    
    try:
        response = requests.get(url, headers=headers, params=params)
        response.raise_for_status()
        records = response.json().get("records", [])
        
        if not records:
            send_telegram_message(chat_id, f"🤷‍♂️ I couldn't find anyone matching '{query}'.")
            return

        if len(records) == 1:
            contact = records[0]["fields"]
            reply = (
                f"👤 <b>{contact.get('Name', 'Unknown')}</b>\n"
                f"💼 {contact.get('Current Role', 'No role listed')}\n"
                f"📍 Met at: {contact.get('Location Met', 'Unknown')}\n"
                f"📅 <b>Action:</b> {contact.get('Action', 'No follow-up needed')}\n"
                f"📧 {contact.get('Email', 'No email')}\n"
                f"🔗 {contact.get('LinkedIn', 'No LinkedIn')}\n\n"
                f"📝 <b>Notes:</b> {contact.get('Context Summary', 'No notes')}"
            )
            send_telegram_message(chat_id, reply)
        else:
            reply = f"🎯 Found {len(records)} matches for '<b>{query}</b>':\n\n"
            for record in records[:5]:
                contact = record["fields"]
                reply += f"🔹 <b>{contact.get('Name', 'Unknown')}</b> - Action: <i>{contact.get('Action', 'None')}</i>\n"
            
            if len(records) > 5:
                reply += f"\n<i>...and {len(records) - 5} more. Try typing a bit more of their name!</i>"
                
            send_telegram_message(chat_id, reply)

    except Exception as e:
        send_telegram_message(chat_id, "⚠️ Error connecting to Airtable search.")
        print(f"Search error: {e}")

def process_and_enrich(raw_text: str, chat_id: int):
    """Orchestrates the background pipeline completely."""
    try:
        send_telegram_message(chat_id, "⏳ Parsing your messy notes...")
        
        parsed_data = parse_with_gemini(raw_text)
        if not parsed_data:
            send_telegram_message(chat_id, "❌ I couldn't understand that note. Try rephrasing?")
            return
            
        name = parsed_data.get("name", "Unknown Contact")
        company = parsed_data.get("company", "")
        location = parsed_data.get("location_met", "")
        action = parsed_data.get("action", "")
        context = parsed_data.get("context_summary", "")
        
        enriched_info = enrich_profile(name, company)
        success = push_to_airtable(name, context, location, action, enriched_info)
        
        if success:
            success_msg = f"✅ <b>Saved {name} to CRM!</b>"
            if action:
                success_msg += f"\n🎯 <b>Reminder:</b> {action}"
            send_telegram_message(chat_id, success_msg)
        else:
            send_telegram_message(chat_id, f"❌ Found {name}, but failed to save to Airtable.")
            
    except Exception as e:
        send_telegram_message(chat_id, "❌ An unexpected error occurred in the pipeline.")
        print(f"❌ Pipeline error: {e}")

@app.post("/webhook")
async def telegram_webhook(request: Request, background_tasks: BackgroundTasks):
    payload = await request.json()
    
    if "message" in payload and "text" in payload["message"]:
        raw_text = payload["message"]["text"]
        chat_id = payload["message"]["chat"]["id"]
        
        if raw_text.startswith("/find"):
            query = raw_text.replace("/find", "").strip()
            if query:
                background_tasks.add_task(search_airtable, query, chat_id)
            else:
                background_tasks.add_task(send_telegram_message, chat_id, "Please include a name! Example: /find John")
        else:
            background_tasks.add_task(process_and_enrich, raw_text, chat_id)
            
        return {"status": "queued"}
    return {"status": "ignored"}

@app.get("/health")
def health(): return {"status": "healthy"}