import os

import requests
from dotenv import load_dotenv


load_dotenv()

DISCORD_WEBHOOK_URL = os.getenv("DISCORD_WEBHOOK_URL")


def send_discord_message(message: str) -> bool:
    if not DISCORD_WEBHOOK_URL:
        print("找不到 DISCORD_WEBHOOK_URL，請檢查 .env")
        return False

    payload = {
        "content": message
    }

    try:
        response = requests.post(
            DISCORD_WEBHOOK_URL,
            json=payload,
            timeout=10
        )

        response.raise_for_status()

        print("Discord 通知發送成功")
        return True

    except requests.RequestException as error:
        print(f"Discord 通知發送失敗：{error}")
        return False