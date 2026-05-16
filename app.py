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
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {"chat_id": chat_id, "text": text, "parse_mode": "HTML"}
    try:
        requests.post(url, json=payload)
    except Exception as e:
        print(f"Failed to send Telegram message: {e}")

def parse_with_gemini(raw_text: str):
    """Uses Gemini to extract full context, including Role, Company, Industry, and Date."""
    print(f"🧠 Asking Gemini to parse: '{raw_text}'...")
    url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent?key={os.getenv('GEMINI_API_KEY')}"
    
    prompt = f"""
    You are an AI assistant for a networking CRM. Analyze this unstructured note: "{raw_text}"
    
    Perform these exact tasks:
    1. Extract the person's first and last name.
    2. Extract their specific job title or role.
    3. Extract their company or organization name.
    4. Infer the general 'Industry' based on their company or our conversation (e.g., 'Fintech', 'AI', 'Venture Capital').
    5. Extract the date/time we met (e.g., 'Yesterday evening', 'Oct 12th', 'Tuesday'). If not mentioned, output 'Unspecified'.
    6. Extract the location where we met.
    7. Rewrite and polish the context of our interaction into a clear, professional summary.
    8. Identify any follow-up task/action required. Leave empty if none.
    
    Return ONLY a valid JSON object with EXACTLY these keys: 
    "name", "role", "company", "industry", "date_met", "location_met", "context_summary", "action".
    Leave values as an empty string if completely unknown. No markdown backticks.
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

def find_linkedin(name: str, company: str):
    """Silent background search for the LinkedIn URL."""
    if not company:
        return ""
    query = f'{name} {company} site:linkedin.com/in/'
    try:
        results = list(DDGS().text(query, max_results=1))
        if results:
            return results[0].get("href", "")
    except Exception:
        pass
    return ""

def push_to_airtable(data: dict, linkedin_url: str):
    """Pushes the comprehensively extracted record into Airtable."""
    print(f"📝 Pushing {data.get('name')} to Airtable...")
    base_id = os.getenv("AIRTABLE_BASE_ID")
    url = f"https://api.airtable.com/v0/{base_id}/Contacts"
    
    headers = {
        "Authorization": f"Bearer {os.getenv('AIRTABLE_PAT')}",
        "Content-Type": "application/json"
    }
    
    payload = {
        "fields": {
            "Name": data.get("name", "Unknown"),
            "Current Role": data.get("role", ""),
            "Company": data.get("company", ""),
            "Industry": data.get("industry", ""),
            "Date Met": data.get("date_met", ""),
            "Location Met": data.get("location_met", ""),
            "Context Summary": data.get("context_summary", ""),
            "Action": data.get("action", ""),
            "LinkedIn": linkedin_url if "http" in linkedin_url else ""
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
    """GLOBAL SEARCH: Scans across Name, Company, Industry, Role, Location, and Notes."""
    send_telegram_message(chat_id, f"🔍 Searching entire CRM for <b>{query}</b>...")
    
    base_id = os.getenv("AIRTABLE_BASE_ID")
    url = f"https://api.airtable.com/v0/{base_id}/Contacts"
    headers = {"Authorization": f"Bearer {os.getenv('AIRTABLE_PAT')}"}
    
    # MAGIC FORMULA: Combines all major fields into one giant string, then searches it.
    formula = f"SEARCH(LOWER('{query}'), LOWER(CONCATENATE({{Name}}, ' ', {{Current Role}}, ' ', {{Company}}, ' ', {{Industry}}, ' ', {{Location Met}}, ' ', {{Context Summary}})))"
    params = {"filterByFormula": formula}
    
    try:
        response = requests.get(url, headers=headers, params=params)
        response.raise_for_status()
        records = response.json().get("records", [])
        
        if not records:
            send_telegram_message(chat_id, f"🤷‍♂️ No results found anywhere in the CRM for '{query}'.")
            return

        if len(records) == 1:
            contact = records[0]["fields"]
            reply = (
                f"👤 <b>{contact.get('Name', 'Unknown')}</b> — <i>{contact.get('Current Role', 'Unknown Role')}</i>\n"
                f"🏢 <b>{contact.get('Company', 'No Company')}</b> ({contact.get('Industry', 'Unknown Industry')})\n"
                f"📍 Met at: {contact.get('Location Met', 'Unknown Location')} on {contact.get('Date Met', 'Unknown Date')}\n"
                f"📅 <b>Action:</b> {contact.get('Action', 'None')}\n"
                f"🔗 {contact.get('LinkedIn', 'No LinkedIn')}\n\n"
                f"📝 <b>Notes:</b> {contact.get('Context Summary', 'No notes')}"
            )
            send_telegram_message(chat_id, reply)
        else:
            reply = f"🎯 Found {len(records)} matches for '<b>{query}</b>':\n\n"
            for record in records[:5]:
                contact = record["fields"]
                reply += f"🔹 <b>{contact.get('Name', 'Unknown')}</b> - {contact.get('Company', 'No Company')} ({contact.get('Industry', 'Industry')})\n"
            
            if len(records) > 5:
                reply += f"\n<i>...and {len(records) - 5} more. Try typing a bit more to narrow it down!</i>"
                
            send_telegram_message(chat_id, reply)

    except Exception as e:
        send_telegram_message(chat_id, "⚠️ Error connecting to Airtable search.")
        print(f"Search error: {e}")

def process_and_enrich(raw_text: str, chat_id: int):
    try:
        send_telegram_message(chat_id, "⏳ Parsing notes and extracting details...")
        
        parsed_data = parse_with_gemini(raw_text)
        if not parsed_data:
            send_telegram_message(chat_id, "❌ I couldn't understand that note. Try rephrasing?")
            return
            
        linkedin_url = find_linkedin(parsed_data.get("name"), parsed_data.get("company"))
        success = push_to_airtable(parsed_data, linkedin_url)
        
        if success:
            success_msg = f"✅ <b>Saved {parsed_data.get('name')} from {parsed_data.get('company')}!</b>"
            if parsed_data.get("action"):
                success_msg += f"\n🎯 <b>Action:</b> {parsed_data.get('action')}"
            send_telegram_message(chat_id, success_msg)
        else:
            send_telegram_message(chat_id, f"❌ Failed to save to Airtable.")
            
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
                background_tasks.add_task(send_telegram_message, chat_id, "Please include a keyword! Example: /find AI or /find Google")
        else:
            background_tasks.add_task(process_and_enrich, raw_text, chat_id)
            
        return {"status": "queued"}
    return {"status": "ignored"}

@app.get("/health")
def health(): return {"status": "healthy"}