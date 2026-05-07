import json
from datetime import datetime
from pathlib import Path
import yt_dlp

# =========================
# CONFIG
# =========================
SOURCE_URL = "https://www.youtube.com/playlist?list=PL88BufbnCzx7TCZ5zRmMhA4Nk1ajLavsV"
VIDEOS_PER_DAY = 15

MODE = "video"
RESOLUTION = "1080"

DOWNLOAD_ROOT = Path("downloads")
ARCHIVE_FILE = Path("downloaded.txt")
STATE_FILE = Path("daily_state.json")
COOKIE_FILE = Path("cookies.txt")
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

def build_format():
    if MODE == "audio":
        return "bestaudio[ext=m4a]/bestaudio/best"
    return f"best[ext=mp4][height<={RESOLUTION}]/best"

def progress_hook(d):
    """
    🔥 REAL-TIME LOGIC: This runs every time yt-dlp finishes a file.
    It updates the JSON immediately so no progress is lost on crash.
    """
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
        # 🔥 Attach the real-time tracker
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

    if remaining == 0:
        print("[-] DAILY LIMIT REACHED")
        return

    format_option = build_format()
    execute_download_pipeline(remaining, format_option)
    
    # Final verification check
    final_state = load_state()
    print(f"\n[+] SESSION COMPLETE. Total downloaded today: {final_state['downloaded_today']}/{VIDEOS_PER_DAY}")

if __name__ == "__main__":
    main()
