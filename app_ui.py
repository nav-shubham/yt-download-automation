import os
import re
import sys
import json
import time
import uuid
import queue
import threading
import webbrowser
from pathlib import Path
from datetime import datetime
from flask import Flask, jsonify, request, render_template, send_from_directory
import yt_dlp

app = Flask(__name__, template_folder='templates', static_folder='static')

# ==========================================
# CONFIG & PATHS
# ==========================================
BASE_DIR = Path(__file__).resolve().parent
DOWNLOAD_ROOT = BASE_DIR / "downloads"
ARCHIVE_FILE = BASE_DIR / "downloaded.txt"
STATE_FILE = BASE_DIR / "daily_state.json"
COOKIE_FILE = BASE_DIR / "cookies.txt"
CSV_FILE = BASE_DIR / "youtube_no_api.csv"
ERROR_LOG = BASE_DIR / "errors.log"
SETTINGS_FILE = BASE_DIR / "settings.json"

# Ensure directories exist
DOWNLOAD_ROOT.mkdir(exist_ok=True)

# Default Settings
DEFAULT_SETTINGS = {
    "download_dir": str(DOWNLOAD_ROOT),
    "max_concurrent": 2,
    "default_resolution": "1080",
    "default_mode": "video",
    "telegram_enabled": False,
    "telegram_bot_token": "",
    "telegram_chat_id": "",
    "concurrent_fragments": 5
}

def load_settings():
    if not SETTINGS_FILE.exists():
        save_settings(DEFAULT_SETTINGS)
        return DEFAULT_SETTINGS
    try:
        settings = json.loads(SETTINGS_FILE.read_text(encoding="utf-8"))
        # Merge missing defaults
        updated = False
        for k, v in DEFAULT_SETTINGS.items():
            if k not in settings:
                settings[k] = v
                updated = True
        if updated:
            save_settings(settings)
        return settings
    except Exception:
        return DEFAULT_SETTINGS

def save_settings(settings):
    try:
        SETTINGS_FILE.write_text(json.dumps(settings, indent=2), encoding="utf-8")
        return True
    except Exception:
        return False

# ==========================================
# QUEUE & TASK MANAGER
# ==========================================
# In-memory database of download tasks
# Key: task_id (str)
# Value: dict containing task details and dynamic progress
tasks_db = {}
tasks_lock = threading.Lock()

# Queue for processing downloads
download_queue = []
active_workers = {} # task_id -> thread object

# Thread controller
queue_condition = threading.Condition()

def get_task_status(task_id):
    with tasks_lock:
        return tasks_db.get(task_id)

def update_task_progress(task_id, updates):
    with tasks_lock:
        if task_id in tasks_db:
            tasks_db[task_id].update(updates)

def format_speed(speed_bytes):
    if speed_bytes is None:
        return "0 B/s"
    if speed_bytes >= 1024 * 1024:
        return f"{speed_bytes / (1024 * 1024):.2f} MB/s"
    elif speed_bytes >= 1024:
        return f"{speed_bytes / 1024:.2f} KB/s"
    else:
        return f"{speed_bytes:.0f} B/s"

def format_eta(eta_seconds):
    if eta_seconds is None:
        return "Unknown"
    if eta_seconds >= 3600:
        h = eta_seconds // 3600
        m = (eta_seconds % 3600) // 60
        s = eta_seconds % 60
        return f"{h:02d}:{m:02d}:{s:02d}"
    else:
        m = eta_seconds // 60
        s = eta_seconds % 60
        return f"{m:02d}:{s:02d}"

def format_size(bytes_val):
    if bytes_val is None:
        return "Unknown"
    if bytes_val >= 1024 * 1024 * 1024:
        return f"{bytes_val / (1024 * 1024 * 1024):.2f} GB"
    elif bytes_val >= 1024 * 1024:
        return f"{bytes_val / (1024 * 1024):.2f} MB"
    elif bytes_val >= 1024:
        return f"{bytes_val / 1024:.2f} KB"
    else:
        return f"{bytes_val} B"

# ==========================================
# YT-DLP DOWNLOAD WORKER
# ==========================================
class DownloadWorker(threading.Thread):
    def __init__(self, task_id):
        super().__init__()
        self.task_id = task_id
        self.daemon = True
        self.stop_requested = False
        self._ydl = None

    def progress_hook(self, d):
        if self.stop_requested:
            # Raise exception to abort yt-dlp download pipeline
            raise Exception("ABORT_REQUESTED")

        task = get_task_status(self.task_id)
        if not task:
            return

        if d['status'] == 'downloading':
            downloaded = d.get('downloaded_bytes', 0)
            total = d.get('total_bytes') or d.get('total_bytes_estimate', 0)
            
            percent = 0.0
            if total > 0:
                percent = (downloaded / total) * 100

            speed = d.get('speed')
            eta = d.get('eta')

            update_task_progress(self.task_id, {
                "status": "downloading",
                "downloaded_bytes": downloaded,
                "total_bytes": total,
                "percent": round(percent, 2),
                "speed_str": format_speed(speed),
                "eta_str": format_eta(eta)
            })
        elif d['status'] == 'finished':
            update_task_progress(self.task_id, {
                "status": "merging",
                "percent": 100.0,
                "speed_str": "0 B/s",
                "eta_str": "00:00"
            })

    def run(self):
        task = get_task_status(self.task_id)
        if not task:
            return

        update_task_progress(self.task_id, {
            "status": "downloading",
            "started_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        })

        settings = load_settings()

        # Build Format
        if task["mode"] == "audio":
            format_option = "bestaudio[ext=m4a]/bestaudio/best"
        else:
            format_option = f"bestvideo[height<={task['resolution']}][ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best"

        out_template = os.path.join(settings["download_dir"], "%(playlist_title)s/%(title)s.%(ext)s")
        if task["is_playlist"]:
            out_template = os.path.join(settings["download_dir"], "%(playlist_title)s/%(playlist_index)03d - %(title)s.%(ext)s")

        ydl_opts = {
            "format": format_option,
            "outtmpl": out_template,
            "download_archive": str(ARCHIVE_FILE),
            "ignoreerrors": True,
            "quiet": True,
            "no_warnings": True,
            "concurrent_fragment_downloads": settings["concurrent_fragments"],
            "retries": 10,
            "fragment_retries": 10,
            "socket_timeout": 30,
            "progress_hooks": [self.progress_hook],
        }

        # Add cookie file if exists
        if COOKIE_FILE.exists():
            ydl_opts["cookiefile"] = str(COOKIE_FILE)

        try:
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                self._ydl = ydl
                # This performs the actual download
                ydl.download([task["url"]])

            # If stopped manually, don't set as completed
            if self.stop_requested:
                update_task_progress(self.task_id, {
                    "status": "paused",
                    "speed_str": "Paused",
                    "eta_str": "--:--"
                })
            else:
                # Retrieve actual filename or save log
                update_task_progress(self.task_id, {
                    "status": "completed",
                    "percent": 100.0,
                    "speed_str": "Completed",
                    "eta_str": "00:00",
                    "completed_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                })
                # Log to CSV if YouTube video
                self.log_to_csv(task)
                # Send telegram notification if configured
                self.notify_telegram(task)
                # Update daily state
                self.update_daily_state()

        except Exception as e:
            if "ABORT_REQUESTED" in str(e):
                update_task_progress(self.task_id, {
                    "status": "paused",
                    "speed_str": "Paused",
                    "eta_str": "--:--"
                })
            else:
                error_msg = str(e)
                update_task_progress(self.task_id, {
                    "status": "failed",
                    "speed_str": "Failed",
                    "eta_str": "--:--",
                    "error": error_msg
                })
                with open(ERROR_LOG, "a", encoding="utf-8") as f:
                    f.write(f"{datetime.now()} | {task['url']} | {error_msg}\n")
        finally:
            with tasks_lock:
                if self.task_id in active_workers:
                    del active_workers[self.task_id]
            # Notify the manager thread that a worker slot has opened
            with queue_condition:
                queue_condition.notify_all()

    def abort(self):
        self.stop_requested = True
        # Try to disrupt yt-dlp socket connections if possible
        if self._ydl:
            pass # yt_dlp handles interruptions via progress hooks elegantly when exceptions are raised

    def log_to_csv(self, task):
        # Extracts metadata and appends to youtube_no_api.csv
        try:
            # Quick extract metadata
            ydl_opts = {
                'quiet': True,
                'skip_download': True,
                'js_runtimes': {'node': {}},
            }
            if COOKIE_FILE.exists():
                ydl_opts["cookiefile"] = str(COOKIE_FILE)

            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(task["url"], download=False)
                
                # Check for playlist or single video
                videos = info.get("entries", [info]) if "entries" in info else [info]
                
                import csv
                file_exists = CSV_FILE.exists()
                
                with open(CSV_FILE, "a", newline="", encoding="utf-8") as f:
                    fieldnames = ["video_id", "title", "channel", "views", "duration", "upload_date", "url", "fetched_at"]
                    writer = csv.DictWriter(f, fieldnames=fieldnames)
                    if not file_exists:
                        writer.writeheader()
                        
                    for vid_info in videos:
                        if not vid_info:
                            continue
                        row = {
                            "video_id": vid_info.get("id", ""),
                            "title": vid_info.get("title", task["title"]),
                            "channel": vid_info.get("uploader", "Unknown"),
                            "views": vid_info.get("view_count", 0),
                            "duration": vid_info.get("duration", 0),
                            "upload_date": vid_info.get("upload_date", ""),
                            "url": f"https://www.youtube.com/watch?v={vid_info.get('id', '')}" if vid_info.get("id") else task["url"],
                            "fetched_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                        }
                        writer.writerow(row)
        except Exception as ex:
            print(f"[!] Error appending CSV log: {ex}")

    def notify_telegram(self, task):
        settings = load_settings()
        if not settings.get("telegram_enabled") or not settings.get("telegram_bot_token"):
            return
        
        token = settings["telegram_bot_token"]
        chat_id = settings["telegram_chat_id"]
        message = f"📥 *Download Complete!*\n*Title:* {task['title']}\n*Mode:* {task['mode']}\n*Resolution:* {task['resolution']}p"
        
        def send_telegram():
            try:
                import requests
                url = f"https://api.telegram.org/bot{token}/sendMessage"
                requests.post(url, json={"chat_id": chat_id, "text": message, "parse_mode": "Markdown"}, timeout=10)
            except Exception as e:
                print(f"[!] Telegram Notification Failed: {e}")

        # Send asynchronously to avoid blocking
        threading.Thread(target=send_telegram, daemon=True).start()

    def update_daily_state(self):
        try:
            today = datetime.now().strftime("%Y-%m-%d")
            state = {"date": today, "downloaded_today": 0}
            if STATE_FILE.exists():
                try:
                    data = json.loads(STATE_FILE.read_text(encoding="utf-8"))
                    if data.get("date") == today:
                        state = data
                except Exception:
                    pass
            state["downloaded_today"] += 1
            STATE_FILE.write_text(json.dumps(state, indent=2), encoding="utf-8")
        except Exception as e:
            print(f"[!] Daily state update failed: {e}")


# ==========================================
# CONCURRENCY QUEUE MANAGER THREAD
# ==========================================
def queue_manager_loop():
    while True:
        settings = load_settings()
        max_concurrent = settings.get("max_concurrent", 2)

        with tasks_lock:
            # Clean terminated workers
            terminated = []
            for t_id, worker in active_workers.items():
                if not worker.is_alive():
                    terminated.append(t_id)
            for t_id in terminated:
                del active_workers[t_id]

            # Collect queued tasks
            queued_tasks = []
            for t_id, task in tasks_db.items():
                if task["status"] == "queued":
                    queued_tasks.append(task)
            
            # Sort by added_at
            queued_tasks.sort(key=lambda x: x["added_at"])

            # Start new workers up to concurrency limit
            available_slots = max_concurrent - len(active_workers)
            for i in range(min(available_slots, len(queued_tasks))):
                task = queued_tasks[i]
                t_id = task["id"]
                
                worker = DownloadWorker(t_id)
                active_workers[t_id] = worker
                # Mark as downloading before thread starts to prevent duplicate runs
                tasks_db[t_id]["status"] = "downloading"
                worker.start()

        # Sleep/Wait until notified of dynamic updates (added, paused, deleted, or worker finishes)
        with queue_condition:
            queue_condition.wait(timeout=2.0)

# Start Queue Manager Thread
threading.Thread(target=queue_manager_loop, daemon=True).start()


# ==========================================
# FLASK WEB ENDPOINTS
# ==========================================

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/api/settings', methods=['GET', 'POST'])
def handle_settings():
    if request.method == 'POST':
        new_settings = request.json
        current = load_settings()
        # Update setting fields safely
        for k in DEFAULT_SETTINGS.keys():
            if k in new_settings:
                if k == "max_concurrent" or k == "concurrent_fragments":
                    current[k] = max(1, int(new_settings[k]))
                elif k == "telegram_enabled":
                    current[k] = bool(new_settings[k])
                else:
                    current[k] = str(new_settings[k])
        save_settings(current)
        with queue_condition:
            queue_condition.notify_all()
        return jsonify({"status": "success", "settings": current})
    else:
        return jsonify(load_settings())

@app.route('/api/fetch-info', methods=['POST'])
def fetch_info():
    data = request.json
    url = data.get("url", "").strip()
    if not url:
        return jsonify({"status": "error", "message": "URL is empty"}), 400

    try:
        ydl_opts = {
            'quiet': True,
            'skip_download': True,
            'js_runtimes': {'node': {}},
        }
        if COOKIE_FILE.exists():
            ydl_opts["cookiefile"] = str(COOKIE_FILE)

        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=False)
            
            # Formulate response object
            is_playlist = "entries" in info
            title = info.get("title", "Unknown Title")
            thumbnail = info.get("thumbnail", "")
            duration_sec = info.get("duration", 0)
            
            # Format duration
            if is_playlist:
                duration_str = f"{len(info.get('entries', []))} videos"
                thumbnail = info.get("entries", [{}])[0].get("thumbnail", "") if info.get("entries") else ""
            else:
                duration_str = format_eta(duration_sec) if duration_sec else "Unknown"

            # Parse formats to list resolutions
            formats = info.get("formats", [])
            resolutions = set()
            for f in formats:
                height = f.get("height")
                if height and height in [144, 240, 360, 480, 720, 1080, 1440, 2160]:
                    resolutions.add(str(height))
            
            # Order resolutions descending
            res_list = sorted(list(resolutions), key=lambda x: int(x), reverse=True)
            if not res_list:
                res_list = ["1080", "720", "480", "360"]

            return jsonify({
                "status": "success",
                "title": title,
                "thumbnail": thumbnail,
                "duration": duration_str,
                "resolutions": res_list,
                "is_playlist": is_playlist,
                "uploader": info.get("uploader", "Unknown")
            })

    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500

@app.route('/api/queue/add', methods=['POST'])
def add_to_queue():
    data = request.json
    url = data.get("url", "").strip()
    if not url:
        return jsonify({"status": "error", "message": "URL is empty"}), 400

    title = data.get("title", "Video Download")
    mode = data.get("mode", "video")
    resolution = data.get("resolution", "1080")
    is_playlist = data.get("is_playlist", False)
    thumbnail = data.get("thumbnail", "")

    task_id = str(uuid.uuid4())
    
    task = {
        "id": task_id,
        "url": url,
        "title": title,
        "mode": mode,
        "resolution": resolution,
        "is_playlist": is_playlist,
        "thumbnail": thumbnail,
        "status": "queued",
        "added_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "started_at": None,
        "completed_at": None,
        "percent": 0.0,
        "downloaded_bytes": 0,
        "total_bytes": 0,
        "speed_str": "Queued",
        "eta_str": "--:--",
        "error": None
    }

    with tasks_lock:
        tasks_db[task_id] = task

    with queue_condition:
        queue_condition.notify_all()

    return jsonify({"status": "success", "task_id": task_id})

@app.route('/api/queue', methods=['GET'])
def get_queue():
    with tasks_lock:
        queue_list = list(tasks_db.values())
    # Sort queue list: downloading first, then queued, then completed/failed by time
    status_order = {"downloading": 0, "merging": 1, "queued": 2, "paused": 3, "completed": 4, "failed": 5}
    queue_list.sort(key=lambda x: (status_order.get(x["status"], 9), x["added_at"]))
    return jsonify(queue_list)

@app.route('/api/queue/control', methods=['POST'])
def control_task():
    data = request.json
    task_id = data.get("task_id")
    action = data.get("action") # "pause", "resume", "delete"

    if not task_id or not action:
        return jsonify({"status": "error", "message": "Missing task_id or action"}), 400

    with tasks_lock:
        task = tasks_db.get(task_id)
        if not task:
            return jsonify({"status": "error", "message": "Task not found"}), 404

        if action == "pause":
            if task["status"] in ["downloading", "queued"]:
                if task_id in active_workers:
                    active_workers[task_id].abort()
                tasks_db[task_id]["status"] = "paused"
                tasks_db[task_id]["speed_str"] = "Paused"
        
        elif action == "resume":
            if task["status"] in ["paused", "failed"]:
                tasks_db[task_id]["status"] = "queued"
                tasks_db[task_id]["speed_str"] = "Queued"
                tasks_db[task_id]["error"] = None

        elif action == "delete":
            # Stop if downloading
            if task_id in active_workers:
                active_workers[task_id].abort()
            del tasks_db[task_id]

    with queue_condition:
        queue_condition.notify_all()

    return jsonify({"status": "success"})

@app.route('/api/history', methods=['GET'])
def get_history():
    # Returns history from CSV if it exists, otherwise empty array
    if not CSV_FILE.exists():
        return jsonify([])
    
    try:
        import csv
        history = []
        with open(CSV_FILE, "r", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                history.append(row)
        # Reverse to show newest first
        history.reverse()
        return jsonify(history[:100]) # Cap at 100 entries for performance
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500

@app.route('/api/stats', methods=['GET'])
def get_stats():
    # Computes advanced dashboard stats
    today = datetime.now().strftime("%Y-%m-%d")
    daily_quota = 40
    downloaded_today = 0

    if STATE_FILE.exists():
        try:
            data = json.loads(STATE_FILE.read_text(encoding="utf-8"))
            if data.get("date") == today:
                downloaded_today = data.get("downloaded_today", 0)
        except Exception:
            pass

    history_count = 0
    if CSV_FILE.exists():
        try:
            with open(CSV_FILE, "r", encoding="utf-8") as f:
                history_count = sum(1 for line in f) - 1 # exclude header
        except Exception:
            pass

    return jsonify({
        "downloaded_today": downloaded_today,
        "daily_quota": daily_quota,
        "total_downloads": history_count
    })

# Start application server
def start_server():
    # Open default browser after a delay to ensure server started
    def open_browser():
        time.sleep(1.5)
        webbrowser.open("http://127.0.0.1:5000")

    threading.Thread(target=open_browser, daemon=True).start()
    app.run(host="127.0.0.1", port=5000, debug=False)

if __name__ == "__main__":
    # Ensure templates and static files are created before running
    # This main section is used for running directly
    print("[*] Engaging Antigravity IDM-Style Download UI Engine...")
    start_server()
