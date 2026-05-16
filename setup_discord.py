import os
import requests
from dotenv import load_dotenv

load_dotenv()
APP_ID = os.getenv("DISCORD_APP_ID")
TOKEN = os.getenv("DISCORD_BOT_TOKEN")

headers = {"Authorization": f"Bot {TOKEN}", "Content-Type": "application/json"}
url = f"https://discord.com/api/v10/applications/{APP_ID}/commands"

commands = [
    {
        "name": "save",
        "description": "Save a new networking note to the CRM",
        "options": [{"name": "note", "description": "Your messy networking notes", "type": 3, "required": True}]
    },
    {
        "name": "find",
        "description": "Search the CRM for a contact",
        "options": [{"name": "query", "description": "Name, company, or industry", "type": 3, "required": True}]
    }
]

for cmd in commands:
    r = requests.post(url, headers=headers, json=cmd)
    print(f"Registered /{cmd['name']}: {r.status_code}")