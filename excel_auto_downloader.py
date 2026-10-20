import json
import requests
from datetime import datetime
from pathlib import Path
from openpyxl import load_workbook
import yt_dlp
from dotenv import load_dotenv
import os
load_dotenv()

# =========================
# CONFIG
# =========================
# Excel file with the specific video links you want downloaded.
# Put one URL per row in the FIRST column of the FIRST sheet.
# A header row (e.g. "URL") is fine - anything that isn't a link is skipped.
VIDEO_LIST_FILE = Path("videos.xlsx")

VIDEOS_PER_DAY = 200

MODE = "video"
RESOLUTION = "1080"

DOWNLOAD_ROOT = Path("downloads")
ARCHIVE_FILE = Path("downloaded.txt")
STATE_FILE = Path("daily_state.json")
COOKIE_FILE = Path("cookies.txt")

# Telegram Config
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")
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

def load_video_urls_from_excel(path: Path) -> list:
    """
    Reads video URLs from the first column of the first sheet of an
    Excel file. Skips empty cells and any non-URL cell (e.g. a header
    row like "URL" or "Link").
    """
    if not path.exists():
        raise FileNotFoundError(f"Video list file not found: {path}")

    wb = load_workbook(filename=str(path), read_only=True, data_only=True)
    try:
        ws = wb.active
        urls = []
        for row in ws.iter_rows(min_col=1, max_col=1, values_only=True):
            cell = row[0]
            if cell is None:
                continue
            cell = str(cell).strip()
            if cell.lower().startswith("http"):
                urls.append(cell)
        return urls
    finally:
        wb.close()

def progress_hook(d):
    if d['status'] == 'finished':
        state = load_state()
        state["downloaded_today"] += 1
        save_state(state)
        print(f"[*] State Updated: {state['downloaded_today']}/{VIDEOS_PER_DAY} recorded.")

def execute_download_pipeline(urls, remaining, format_option):
    DOWNLOAD_ROOT.mkdir(exist_ok=True)

    ydl_opts = {
        "format": format_option,
        "outtmpl": f"{DOWNLOAD_ROOT}/%(title)s.%(ext)s",
        "download_archive": str(ARCHIVE_FILE),
        "max_downloads": remaining,
        "ignoreerrors": True,
        "quiet": False,
        "no_warnings": True,
        "cookiefile": str(COOKIE_FILE),
        # Each row is a single specific video. Without this, a URL that
        # still carries a "&list=..." param (common when you copy links
        # straight out of a playlist) would make yt-dlp pull in the
        # whole playlist instead of just that one video.
        "noplaylist": True,
        "concurrent_fragment_downloads": 5,
        "retries": 10,
        "fragment_retries": 10,
        "socket_timeout": 30,
        "progress_hooks": [progress_hook],
    }

    print(f"[*] Native Engine Engaged. {len(urls)} link(s) queued from {VIDEO_LIST_FILE.name}, daily quota remaining: {remaining}.")

    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        try:
            ydl.download(urls)
        except yt_dlp.utils.MaxDownloadsReached:
            print("[*] Quota achieved. Controlled shutdown.")
        except Exception as e:
            print(f"[!] Engine friction: {e}")
            raise

def main():
    state = load_state()
    remaining = max(0, VIDEOS_PER_DAY - state["downloaded_today"])

    # Scenario 1: Limit already reached
    if remaining == 0:
        msg = f"ℹ️ *System Idle*\nDaily limit of {VIDEOS_PER_DAY} already reached for {get_today_str()}."
        print(f"[-] {msg}")
        send_telegram_notification(msg)
        return

    # Load the specific videos to download from the Excel file
    try:
        urls = load_video_urls_from_excel(VIDEO_LIST_FILE)
    except Exception as e:
        msg = f"❌ *Video List Error*\nCouldn't read `{VIDEO_LIST_FILE}`: `{e}`"
        print(f"[!] {msg}")
        send_telegram_notification(msg)
        return

    if not urls:
        msg = f"⚠️ *Empty Video List*\nNo valid URLs found in `{VIDEO_LIST_FILE}`."
        print(f"[!] {msg}")
        send_telegram_notification(msg)
        return

    # Scenario 2: Active Downloading
    format_option = build_format()
    execute_download_pipeline(urls, remaining, format_option)

    # Final verification check
    final_state = load_state()
    summary_text = (
        f"✅ *Session Complete*\n"
        f"Downloaded today: `{final_state['downloaded_today']}/{VIDEOS_PER_DAY}`"
    )

    print(f"\n[+] {summary_text}")
    send_telegram_notification(summary_text)


if __name__ == "__main__":
    start_state = load_state()
    error_msg = None

    try:
        main()

    except KeyboardInterrupt:
        error_msg = "⚠️ *Interrupted Manually*\nScript stopped by user (KeyboardInterrupt)."

    except Exception as e:
        error_msg = f"❌ *Unexpected Crash*\nError: `{str(e)}`"

    finally:
        # Always runs (success OR failure OR crash)
        final_state = load_state()

        if error_msg:
            message = (
                f"{error_msg}\n\n"
                f"📊 *Progress Summary*\n"
                f"Downloaded today: `{final_state['downloaded_today']}/{VIDEOS_PER_DAY}`"
            )
        else:
            message = (
                f"✅ *Script Exit (Normal)*\n"
                f"📊 Downloaded today: `{final_state['downloaded_today']}/{VIDEOS_PER_DAY}`"
            )

        print("[*] Final Notification Triggered.")
        send_telegram_notification(message)
