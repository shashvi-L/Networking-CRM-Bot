import os
import sys
import requests
from dotenv import load_dotenv

load_dotenv()

token = os.getenv("TELEGRAM_BOT_TOKEN")
if not token:
    print("Error: TELEGRAM_BOT_TOKEN not found in .env file.")
    sys.exit(1)

if len(sys.argv) < 2:
    print("Usage: python setup_webhook.py <YOUR_PUBLIC_SERVER_URL>")
    print("Example: python setup_webhook.py https://xyz.ngrok-free.app")
    sys.exit(1)

public_url = sys.argv[1].rstrip("/")
webhook_url = f"{public_url}/webhook"
telegram_api_url = f"https://api.telegram.org/bot{token}/setWebhook"

print(f"Registering webhook to: {webhook_url} ...")
response = requests.post(telegram_api_url, data={"url": webhook_url})

if response.status_code == 200 and response.json().get("ok"):
    print("Success! Your Telegram bot is now securely linked to Networking-CRM-Bot.")
else:
    print(f"Failed to set webhook. Telegram response: {response.text}")