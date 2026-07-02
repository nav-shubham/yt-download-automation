import json
import requests
from datetime import datetime
from pathlib import Path
import yt_dlp

# =========================
# CONFIG
# =========================
SOURCE_URL = "https://www.youtube.com/playlist?list=PLNHsPXtL9FFRmOWY4aurbSfNQ_17ODdt9"
VIDEOS_PER_DAY = 45

MODE = "video"
RESOLUTION = "1080"

DOWNLOAD_ROOT = Path("downloads")
ARCHIVE_FILE = Path("downloaded.txt")
STATE_FILE = Path("daily_state.json")
COOKIE_FILE = Path("cookies.txt")

# Telegram Config
TELEGRAM_BOT_TOKEN = "8729490850:AAFKLvzE7DTTp67dbf8ejsVKYP4SIDIyKkE"
TELEGRAM_CHAT_ID = "752824992"
# =========================

def get_today_str():
    return datetime.now().strftime("%Y-%m-%d")

def load_state():
    if not STATE_FILE.exists():
        return {"date": get_today_str(), "downloaded_today": 0}
    try:
        data = json.loads(STATE_FILE.read_text(encoding="utf-8"))
        if data.get("date") != get_today_str():
            return {"date": get_today_str(), "downloaded_today": 0}
        return data
    except:
        return {"date": get_today_str(), "downloaded_today": 0}

def save_state(state):
    STATE_FILE.write_text(json.dumps(state, indent=2), encoding="utf-8")

def send_telegram_notification(message):
    """
    Executes a non-blocking POST request to the Telegram API.
    Fails gracefully to preserve the core script operation.
    """
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": message,
        "parse_mode": "Markdown"
    }
    try:
        response = requests.post(url, json=payload, timeout=10)
        response.raise_for_status()
        print("[*] Telegram telemetry transmitted successfully.")
    except Exception as e:
        print(f"[!] Telemetry failure (Telegram API): {e}")

def build_format():
    if MODE == "audio":
        return "bestaudio[ext=m4a]/bestaudio/best"
    return f"best[ext=mp4][height<={RESOLUTION}]/best"

def progress_hook(d):
    if d['status'] == 'finished':
        state = load_state()
        state["downloaded_today"] += 1
        save_state(state)
        print(f"[*] State Updated: {state['downloaded_today']}/{VIDEOS_PER_DAY} recorded.")

def execute_download_pipeline(remaining, format_option):
    DOWNLOAD_ROOT.mkdir(exist_ok=True)

    ydl_opts = {
        "format": format_option,
        "outtmpl": f"{DOWNLOAD_ROOT}/%(playlist_title)s/%(playlist_index)03d - %(title)s.%(ext)s",
        "download_archive": str(ARCHIVE_FILE),
        "max_downloads": remaining, 
        "ignoreerrors": True,
        "quiet": False,         
        "no_warnings": True,
        "cookiefile": str(COOKIE_FILE),
        "concurrent_fragment_downloads": 5, 
        "retries": 10,
        "fragment_retries": 10,
        "socket_timeout": 30,
        "progress_hooks": [progress_hook],
    }

    print(f"[*] Native Engine Engaged. Target quota: {remaining} videos.")
    
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        try:
            ydl.download([SOURCE_URL])
        except yt_dlp.utils.MaxDownloadsReached:
            print("[*] Quota achieved. Controlled shutdown.")
        except Exception as e:
            print(f"[!] Engine friction: {e}")

def main():
    state = load_state()
    remaining = max(0, VIDEOS_PER_DAY - state["downloaded_today"])

    # Scenario 1: Limit already reached
    if remaining == 0:
        msg = f"ℹ️ *System Idle*\nDaily limit of {VIDEOS_PER_DAY} already reached for {get_today_str()}."
        print(f"[-] {msg}")
        send_telegram_notification(msg) # Added here to fix the issue
        return

    # Scenario 2: Active Downloading
    format_option = build_format()
    execute_download_pipeline(remaining, format_option)
    
    # Final verification check
    final_state = load_state()
    summary_text = (
        f"✅ *Session Complete*\n"
        f"Downloaded today: `{final_state['downloaded_today']}/{VIDEOS_PER_DAY}`"
    )
    
    print(f"\n[+] {summary_text}")
    send_telegram_notification(summary_text)
if __name__ == "__main__":
    main()
