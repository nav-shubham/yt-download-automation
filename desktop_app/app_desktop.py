import os
import sys
import json
import uuid
import queue
import threading
import time
from pathlib import Path
from datetime import datetime
import customtkinter as ctk
from tkinter import messagebox, filedialog
import yt_dlp

# Set appearance mode & default color theme
ctk.set_appearance_mode("dark")
ctk.set_default_color_theme("blue")

# ==========================================
# CONFIG & PATHS
# ==========================================
BASE_DIR = Path(__file__).resolve().parent.parent
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
# THREAD-SAFE STATE DATABASE
# ==========================================
tasks_db = {}
tasks_lock = threading.Lock()
active_workers = {} # task_id -> DownloadWorker
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

# ==========================================
# BACKGROUND DOWNLOAD WORKER (YT-DLP)
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
                "percent": round(percent, 2),
                "speed_str": format_speed(speed),
                "eta_str": format_eta(eta)
            })
        elif d['status'] == 'finished':
            update_task_progress(self.task_id, {
                "status": "merging",
                "percent": 100.0,
                "speed_str": "Merging...",
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

        if COOKIE_FILE.exists():
            ydl_opts["cookiefile"] = str(COOKIE_FILE)

        try:
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                self._ydl = ydl
                ydl.download([task["url"]])

            if self.stop_requested:
                update_task_progress(self.task_id, {
                    "status": "paused",
                    "speed_str": "Paused",
                    "eta_str": "--:--"
                })
            else:
                update_task_progress(self.task_id, {
                    "status": "completed",
                    "percent": 100.0,
                    "speed_str": "Completed",
                    "eta_str": "00:00",
                    "completed_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                })
                self.log_to_csv(task)
                self.notify_telegram(task)
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
            with queue_condition:
                queue_condition.notify_all()

    def abort(self):
        self.stop_requested = True

    def log_to_csv(self, task):
        try:
            ydl_opts = {
                'quiet': True,
                'skip_download': True,
                'js_runtimes': {'node': {}},
            }
            if COOKIE_FILE.exists():
                ydl_opts["cookiefile"] = str(COOKIE_FILE)

            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(task["url"], download=False)
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
            print(f"[!] Error logging completed task: {ex}")

    def notify_telegram(self, task):
        settings = load_settings()
        if not settings.get("telegram_enabled") or not settings.get("telegram_bot_token"):
            return
        
        token = settings["telegram_bot_token"]
        chat_id = settings["telegram_chat_id"]
        message = f"📥 [Desktop App] *Download Complete!*\n*Title:* {task['title']}\n*Quality:* {task['resolution']}p"
        
        def send_telegram():
            try:
                import requests
                url = f"https://api.telegram.org/bot{token}/sendMessage"
                requests.post(url, json={"chat_id": chat_id, "text": message, "parse_mode": "Markdown"}, timeout=10)
            except Exception:
                pass
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
        except Exception:
            pass

# ==========================================
# THREADED QUEUE CONCURRENCY CONTROLLER
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

            # Start workers up to limit
            available_slots = max_concurrent - len(active_workers)
            for i in range(min(available_slots, len(queued_tasks))):
                task = queued_tasks[i]
                t_id = task["id"]
                
                worker = DownloadWorker(t_id)
                active_workers[t_id] = worker
                tasks_db[t_id]["status"] = "downloading"
                worker.start()

        with queue_condition:
            queue_condition.wait(timeout=2.0)

# Start background queue manager thread
threading.Thread(target=queue_manager_loop, daemon=True).start()

# ==========================================
# MAIN NATIVE DESKTOP GUI APPLICATION
# ==========================================
class AntigravityIDMApp(ctk.CTk):
    def __init__(self):
        super().__init__()
        
        # Window Configuration
        self.title("Antigravity Downloader - Native IDM Desktop")
        self.geometry("1080x700")
        self.minsize(950, 600)
        
        # Cache for fetched URL metadata
        self.active_metadata = None
        
        # Configure layout system grid (1 row, 2 columns)
        self.grid_rowconfigure(0, weight=1)
        self.grid_columnconfigure(1, weight=1)
        
        # 1. SIDEBAR PANEL (Col 0)
        self.setup_sidebar()
        
        # 2. MAIN CONTAINER FRAMES (Col 1)
        # We will create dedicated CTkFrame panels for each screen and switch between them!
        self.main_container = ctk.CTkFrame(self, fg_color="transparent")
        self.main_container.grid(row=0, column=1, sticky="nsew", padx=20, pady=20)
        self.main_container.grid_rowconfigure(0, weight=1)
        self.main_container.grid_columnconfigure(0, weight=1)
        
        self.panels = {}
        self.setup_dashboard_panel()
        self.setup_queue_panel()
        self.setup_history_panel()
        self.setup_settings_panel()
        
        # Show dashboard initially
        self.switch_panel("dashboard")
        
        # Start GUI polling updates loop
        self.poll_gui_updates()

    # ==========================================
    # SIDEBAR SETUP
    # ==========================================
    def setup_sidebar(self):
        self.sidebar_frame = ctk.CTkFrame(self, width=220, corner_radius=0)
        self.sidebar_frame.grid(row=0, column=0, sticky="nsew")
        self.sidebar_frame.grid_rowconfigure(5, weight=1)
        
        # Sidebar Logo
        self.logo_label = ctk.CTkLabel(
            self.sidebar_frame, 
            text="ANTIGRAVITY", 
            font=ctk.CTkFont(family="Helvetica", size=22, weight="bold")
        )
        self.logo_label.grid(row=0, column=0, padx=20, pady=(25, 5), sticky="w")
        
        self.sublogo_label = ctk.CTkLabel(
            self.sidebar_frame, 
            text="IDM DESKTOP ENGINE", 
            font=ctk.CTkFont(size=9, weight="bold"),
            text_color="#00f2fe"
        )
        self.sublogo_label.grid(row=1, column=0, padx=20, pady=(0, 35), sticky="w")
        
        # Sidebar Navigation Buttons
        self.nav_buttons = {}
        nav_specs = [
            ("dashboard", "Dashboard", "📊"),
            ("queue", "Active Queue", "📋"),
            ("history", "Completed Logs", "💾"),
            ("settings", "Settings", "⚙️")
        ]
        
        for idx, (panel_id, label, icon) in enumerate(nav_specs):
            btn = ctk.CTkButton(
                self.sidebar_frame,
                text=f"  {icon}  {label}",
                font=ctk.CTkFont(size=14, weight="normal"),
                height=42,
                anchor="w",
                fg_color="transparent",
                text_color="gray75",
                hover_color="gray25",
                corner_radius=8,
                command=lambda p=panel_id: self.switch_panel(p)
            )
            btn.grid(row=idx + 2, column=0, padx=15, pady=6, sticky="ew")
            self.nav_buttons[panel_id] = btn

        # Sidebar Footer
        self.status_frame = ctk.CTkFrame(self.sidebar_frame, fg_color="transparent")
        self.status_frame.grid(row=6, column=0, padx=20, pady=25, sticky="ew")
        
        self.status_indicator = ctk.CTkLabel(
            self.status_frame, 
            text="● Core Engine Online", 
            font=ctk.CTkFont(size=12, weight="bold"), 
            text_color="#10b981"
        )
        self.status_indicator.pack(anchor="w")

    def switch_panel(self, panel_id):
        # Hide all panel frames
        for panel in self.panels.values():
            panel.grid_forget()
        
        # Show target panel frame
        self.panels[panel_id].grid(row=0, column=0, sticky="nsew")
        
        # Update menu buttons styling
        for btn_id, btn in self.nav_buttons.items():
            if btn_id == panel_id:
                btn.configure(fg_color="#1f538d", text_color="white")
            else:
                btn.configure(fg_color="transparent", text_color="gray75")

        # Dynamic loads
        if panel_id == "history":
            self.load_history_list()
        elif panel_id == "settings":
            self.load_settings_into_form()

    # ==========================================
    # DASHBOARD PANEL (Downloader link adder)
    # ==========================================
    def setup_dashboard_panel(self):
        panel = ctk.CTkFrame(self.main_container, fg_color="transparent")
        self.panels["dashboard"] = panel
        
        # Title
        title_lbl = ctk.CTkLabel(panel, text="Dashboard Downloader", font=ctk.CTkFont(size=24, weight="bold"))
        title_lbl.pack(anchor="w", pady=(10, 2))
        
        subtitle_lbl = ctk.CTkLabel(panel, text="Paste media URL to retrieve stream configurations", text_color="gray60", font=ctk.CTkFont(size=13))
        subtitle_lbl.pack(anchor="w", pady=(0, 20))
        
        # 1. Add URL Card Box
        input_card = ctk.CTkFrame(panel, border_width=1, border_color="#1f538d")
        input_card.pack(fill="x", pady=10, padx=2)
        
        card_title = ctk.CTkLabel(input_card, text="🔗  Paste Media/Playlist Link", font=ctk.CTkFont(size=15, weight="bold"))
        card_title.pack(anchor="w", padx=20, pady=(15, 8))
        
        input_row = ctk.CTkFrame(input_card, fg_color="transparent")
        input_row.pack(fill="x", padx=20, pady=(0, 20))
        
        self.url_entry = ctk.CTkEntry(
            input_row, 
            placeholder_text="Enter YouTube Video or Playlist URL here...",
            height=45,
            font=ctk.CTkFont(size=14)
        )
        self.url_entry.pack(side="left", fill="x", expand=True, padx=(0, 15))
        self.url_entry.bind("<Return>", lambda e: self.start_metadata_analysis())
        
        self.analyze_btn = ctk.CTkButton(
            input_row,
            text="Analyze URL",
            font=ctk.CTkFont(size=14, weight="bold"),
            height=45,
            width=130,
            command=self.start_metadata_analysis
        )
        self.analyze_btn.pack(side="right")
        
        # Loading Indicator (Hidden initially)
        self.loader_frame = ctk.CTkFrame(panel, fg_color="transparent")
        self.loader_label = ctk.CTkLabel(
            self.loader_frame, 
            text="⌛ Querying streaming profiles and parsing segments... Please wait.", 
            text_color="#00f2fe",
            font=ctk.CTkFont(size=13, weight="normal")
        )
        self.loader_label.pack(pady=10)
        
        # 2. Metadata Extraction Card (Hidden initially)
        self.meta_card = ctk.CTkFrame(panel)
        
        meta_inner = ctk.CTkFrame(self.meta_card, fg_color="transparent")
        meta_inner.pack(fill="both", expand=True, padx=20, pady=20)
        
        # Left Info section / Right Configurations
        self.meta_title_lbl = ctk.CTkLabel(
            meta_inner, 
            text="Video Title Placeholder", 
            font=ctk.CTkFont(size=16, weight="bold"),
            anchor="w",
            wraplength=600,
            justify="left"
        )
        self.meta_title_lbl.pack(anchor="w", pady=(0, 5))
        
        self.meta_channel_lbl = ctk.CTkLabel(
            meta_inner, 
            text="Channel Name • 00:00 Duration", 
            text_color="gray60", 
            font=ctk.CTkFont(size=13, weight="normal"),
            anchor="w"
        )
        self.meta_channel_lbl.pack(anchor="w", pady=(0, 20))
        
        # Grid settings fields
        config_grid = ctk.CTkFrame(meta_inner, fg_color="transparent")
        config_grid.pack(fill="x", pady=5)
        
        ctk.CTkLabel(config_grid, text="Format Mode:", font=ctk.CTkFont(size=12, weight="bold"), text_color="gray70").grid(row=0, column=0, sticky="w", pady=5, padx=(0, 15))
        self.mode_menu = ctk.CTkOptionMenu(
            config_grid, 
            values=["Video + Audio (Merged)", "Audio Only (M4A)"],
            width=220,
            command=self.on_mode_preset_changed
        )
        self.mode_menu.grid(row=0, column=1, sticky="w", pady=5)
        
        self.res_lbl = ctk.CTkLabel(config_grid, text="Target Resolution:", font=ctk.CTkFont(size=12, weight="bold"), text_color="gray70")
        self.res_lbl.grid(row=1, column=0, sticky="w", pady=5, padx=(0, 15))
        self.res_menu = ctk.CTkOptionMenu(config_grid, values=["1080p", "720p", "480p", "360p"], width=220)
        self.res_menu.grid(row=1, column=1, sticky="w", pady=5)
        
        # Download Actions Row
        actions_row = ctk.CTkFrame(meta_inner, fg_color="transparent")
        actions_row.pack(fill="x", pady=(25, 0))
        
        self.dl_now_btn = ctk.CTkButton(
            actions_row, 
            text="📥  Download Now", 
            fg_color="#10b981", 
            hover_color="#059669",
            font=ctk.CTkFont(size=14, weight="bold"),
            height=40,
            width=160,
            command=lambda: self.add_to_download_queue(start_now=True)
        )
        self.dl_now_btn.pack(side="left", padx=(0, 15))
        
        self.queue_btn = ctk.CTkButton(
            actions_row, 
            text="⏳  Add to Queue", 
            fg_color="gray25", 
            hover_color="gray30",
            font=ctk.CTkFont(size=14, weight="bold"),
            height=40,
            width=160,
            command=lambda: self.add_to_download_queue(start_now=False)
        )
        self.queue_btn.pack(side="left")

        # Bottom Area - Quota Details Card
        quota_card = ctk.CTkFrame(panel)
        quota_card.pack(fill="x", side="bottom", pady=(20, 10), padx=2)
        
        quota_inner = ctk.CTkFrame(quota_card, fg_color="transparent")
        quota_inner.pack(fill="both", expand=True, padx=20, pady=15)
        
        self.quota_lbl = ctk.CTkLabel(
            quota_inner, 
            text="Daily Limit Allowance Monitor", 
            font=ctk.CTkFont(size=14, weight="bold")
        )
        self.quota_lbl.pack(anchor="w")
        
        self.quota_pb = ctk.CTkProgressBar(quota_inner, height=8)
        self.quota_pb.pack(fill="x", pady=10)
        
        self.quota_desc = ctk.CTkLabel(
            quota_inner, 
            text="Loaded: 0 / 40 allowance slots downloaded today.", 
            text_color="gray60",
            font=ctk.CTkFont(size=12)
        )
        self.quota_desc.pack(anchor="w")

    def on_mode_preset_changed(self, choice):
        if "Audio" in choice:
            self.res_lbl.grid_remove()
            self.res_menu.grid_remove()
        else:
            self.res_lbl.grid()
            self.res_menu.grid()

    # ==========================================
    # METADATA ANALYSIS WORKER (THREADED)
    # ==========================================
    def start_metadata_analysis(self):
        url = self.url_entry.get().strip()
        if not url:
            messagebox.showerror("Validation Error", "Please paste a video or playlist URL first.")
            return

        self.loader_frame.pack(pady=10)
        self.meta_card.pack_forget()
        self.analyze_btn.configure(state="disabled")

        def run():
            try:
                ydl_opts = {'quiet': True, 'skip_download': True, 'js_runtimes': {'node': {}}}
                if COOKIE_FILE.exists():
                    ydl_opts["cookiefile"] = str(COOKIE_FILE)

                with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                    info = ydl.extract_info(url, download=False)
                    is_playlist = "entries" in info
                    title = info.get("title", "Unknown Title")
                    duration_sec = info.get("duration", 0)
                    uploader = info.get("uploader", "Unknown Uploader")

                    if is_playlist:
                        duration_str = f"{len(info.get('entries', []))} videos"
                    else:
                        m = duration_sec // 60
                        s = duration_sec % 60
                        duration_str = f"{m:02d}:{s:02d}"

                    # Parse resolutions list
                    formats = info.get("formats", [])
                    resolutions = set()
                    for f in formats:
                        h = f.get("height")
                        if h and h in [144, 240, 360, 480, 720, 1080, 1440, 2160]:
                            resolutions.add(f"{h}p")
                    
                    res_list = sorted(list(resolutions), key=lambda x: int(x.replace('p','')), reverse=True)
                    if not res_list:
                        res_list = ["1080p", "720p", "480p", "360p"]

                    # Update GUI in main thread safely
                    self.after(0, lambda: self.show_metadata_card(
                        url, title, uploader, duration_str, is_playlist, res_list, info.get("thumbnail", "")
                    ))

            except Exception as e:
                self.after(0, lambda: messagebox.showerror("Analysis Error", f"Failed to retrieve stream metadata: {e}"))
                self.after(0, lambda: self.loader_frame.pack_forget())
            finally:
                self.after(0, lambda: self.analyze_btn.configure(state="normal"))

        threading.Thread(target=run, daemon=True).start()

    def show_metadata_card(self, url, title, uploader, duration_str, is_playlist, res_list, thumbnail_url):
        self.loader_frame.pack_forget()
        
        self.active_metadata = {
            "url": url,
            "title": title,
            "is_playlist": is_playlist,
            "thumbnail": thumbnail_url
        }
        
        self.meta_title_lbl.configure(text=title)
        self.meta_channel_lbl.configure(text=f"{uploader}  •  {duration_str}")
        
        # Populate OptionMenus
        clean_res = [r.replace('p','') for r in res_list]
        self.res_menu.configure(values=clean_res)
        self.res_menu.set(clean_res[0])
        
        # Default options
        settings = load_settings()
        if settings["default_resolution"] in clean_res:
            self.res_menu.set(settings["default_resolution"])

        # Display Card
        self.meta_card.pack(fill="x", pady=20, padx=2)

    def add_to_download_queue(self, start_now):
        if not self.active_metadata:
            return
        
        choice_mode = "audio" if "Audio" in self.mode_menu.get() else "video"
        choice_res = self.res_menu.get()

        task_id = str(uuid.uuid4())
        task = {
            "id": task_id,
            "url": self.active_metadata["url"],
            "title": self.active_metadata["title"],
            "mode": choice_mode,
            "resolution": choice_res,
            "is_playlist": self.active_metadata["is_playlist"],
            "thumbnail": self.active_metadata["thumbnail"],
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

        # Clear input box and hide result card
        self.url_entry.delete(0, 'end')
        self.meta_card.pack_forget()
        self.active_metadata = None

        # Alert the background manager thread
        with queue_condition:
            queue_condition.notify_all()

        messagebox.showinfo("Queue System", "Task successfully registered in background queue!")
        
        if start_now:
            self.switch_panel("queue")
        else:
            self.refresh_quota_status()

    # ==========================================
    # ACTIVE QUEUE TAB PANEL
    # ==========================================
    def setup_queue_panel(self):
        panel = ctk.CTkFrame(self.main_container, fg_color="transparent")
        self.panels["queue"] = panel
        
        # Header Info Row
        header_row = ctk.CTkFrame(panel, fg_color="transparent")
        header_row.pack(fill="x", pady=(10, 15))
        
        ctk.CTkLabel(header_row, text="Active Extraction Queue", font=ctk.CTkFont(size=24, weight="bold")).pack(side="left")
        self.concurrency_lbl = ctk.CTkLabel(header_row, text="Max Concurrent: 2", text_color="gray60", font=ctk.CTkFont(size=13, weight="normal"))
        self.concurrency_lbl.pack(side="right")
        
        # Scrollable container for rows
        self.queue_scroll = ctk.CTkScrollableFrame(panel, border_width=1, border_color="gray25")
        self.queue_scroll.pack(fill="both", expand=True)
        
        # Dictionary to cache row widgets to prevent redraw lag
        # Key: task_id (str)
        # Value: dict of components
        self.queue_widget_cache = {}

    def rebuild_queue_gui(self):
        # Read a copy of tasks to build UI
        with tasks_lock:
            tasks = list(tasks_db.values())
        
        status_order = {"downloading": 0, "merging": 1, "queued": 2, "paused": 3, "completed": 4, "failed": 5}
        tasks.sort(key=lambda x: (status_order.get(x["status"], 9), x["added_at"]))
        
        # Remove widgets from cache that are no longer in DB
        active_ids = {t["id"] for t in tasks}
        to_delete = [t_id for t_id in self.queue_widget_cache.keys() if t_id not in active_ids]
        for t_id in to_delete:
            self.queue_widget_cache[t_id]["frame"].destroy()
            del self.queue_widget_cache[t_id]

        if not tasks:
            # Clear inner scroll children
            for child in self.queue_scroll.winfo_children():
                child.destroy()
            self.queue_widget_cache.clear()
            
            empty_lbl = ctk.CTkLabel(self.queue_scroll, text="No active or pending download tasks in queue.", text_color="gray60", font=ctk.CTkFont(size=14))
            empty_lbl.pack(pady=40)
            return

        # Ensure no empty states lingering
        for child in self.queue_scroll.winfo_children():
            if isinstance(child, ctk.CTkLabel) and "No active" in child.cget("text"):
                child.destroy()

        for idx, task in enumerate(tasks):
            t_id = task["id"]
            percent = task["percent"] / 100.0
            
            # Formatted tags
            quality = "Audio" if task["mode"] == "audio" else f"{task['resolution']}p"
            title_text = task["title"]
            if len(title_text) > 55:
                title_text = title_text[:52] + "..."
                
            status_text = task["status"].upper()
            speed_txt = task["speed_str"]
            eta_txt = f"ETA: {task['eta_str']}"

            # Create new row widgets if missing
            if t_id not in self.queue_widget_cache:
                row_frame = ctk.CTkFrame(self.queue_scroll, corner_radius=10, border_width=1, border_color="gray20")
                row_frame.pack(fill="x", pady=6, padx=5)
                
                # Column layouts (title/progress/actions)
                title_col = ctk.CTkFrame(row_frame, fg_color="transparent")
                title_col.pack(side="left", fill="y", padx=15, pady=10)
                
                title_lbl = ctk.CTkLabel(title_col, text=title_text, font=ctk.CTkFont(size=13, weight="bold"), anchor="w")
                title_lbl.pack(anchor="w")
                
                tag_lbl = ctk.CTkLabel(title_col, text=f"🏷️  {quality} Mode • {status_text}", text_color="#00f2fe", font=ctk.CTkFont(size=11, weight="bold"))
                tag_lbl.pack(anchor="w", pady=(2, 0))
                
                # Progress and Speed section
                progress_col = ctk.CTkFrame(row_frame, fg_color="transparent")
                progress_col.pack(side="left", fill="both", expand=True, padx=20, pady=10)
                
                pb = ctk.CTkProgressBar(progress_col, height=6)
                pb.set(percent)
                pb.pack(fill="x", pady=(5, 3))
                
                metrics_lbl = ctk.CTkLabel(
                    progress_col, 
                    text=f"{task['percent']}% • {speed_txt} • {eta_txt}", 
                    font=ctk.CTkFont(size=11),
                    text_color="gray60"
                )
                metrics_lbl.pack(anchor="w")
                
                # Action Buttons Frame
                btn_col = ctk.CTkFrame(row_frame, fg_color="transparent")
                btn_col.pack(side="right", padx=15, pady=10)
                
                pause_btn = ctk.CTkButton(
                    btn_col, 
                    text="⏸️", 
                    width=32, 
                    height=32, 
                    fg_color="gray25", 
                    hover_color="gray30",
                    command=lambda i=t_id: self.trigger_task_control(i, "pause")
                )
                pause_btn.pack(side="left", padx=3)
                
                resume_btn = ctk.CTkButton(
                    btn_col, 
                    text="▶️", 
                    width=32, 
                    height=32, 
                    fg_color="gray25", 
                    hover_color="gray30",
                    command=lambda i=t_id: self.trigger_task_control(i, "resume")
                )
                resume_btn.pack(side="left", padx=3)
                
                delete_btn = ctk.CTkButton(
                    btn_col, 
                    text="❌", 
                    width=32, 
                    height=32, 
                    fg_color="gray25", 
                    hover_color="#ef4444",
                    command=lambda i=t_id: self.trigger_task_control(i, "delete")
                )
                delete_btn.pack(side="left", padx=3)
                
                self.queue_widget_cache[t_id] = {
                    "frame": row_frame,
                    "title": title_lbl,
                    "tag": tag_lbl,
                    "pb": pb,
                    "metrics": metrics_lbl,
                    "pause": pause_btn,
                    "resume": resume_btn,
                    "delete": delete_btn
                }
            else:
                # Update existing row widgets
                cache = self.queue_widget_cache[t_id]
                cache["title"].configure(text=title_text)
                cache["tag"].configure(text=f"🏷️  {quality} Mode • {status_text}")
                cache["pb"].set(percent)
                cache["metrics"].configure(text=f"{task['percent']}%  |  {speed_txt}  |  {eta_txt}")
                
            # Toggle Pause/Play buttons visibility depending on task status
            cache = self.queue_widget_cache[t_id]
            if task["status"] in ["downloading", "queued"]:
                cache["pause"].pack(side="left", padx=3)
                cache["resume"].pack_forget()
            elif task["status"] in ["paused", "failed"]:
                cache["pause"].pack_forget()
                cache["resume"].pack(side="left", padx=3)
            else:
                cache["pause"].pack_forget()
                cache["resume"].pack_forget()

    def trigger_task_control(self, task_id, action):
        with tasks_lock:
            task = tasks_db.get(task_id)
            if not task:
                return

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
                if task_id in active_workers:
                    active_workers[task_id].abort()
                del tasks_db[task_id]

        with queue_condition:
            queue_condition.notify_all()
            
        self.rebuild_queue_gui()
        self.refresh_quota_status()

    # ==========================================
    # HISTORY TAB PANEL
    # ==========================================
    def setup_history_panel(self):
        panel = ctk.CTkFrame(self.main_container, fg_color="transparent")
        self.panels["history"] = panel
        
        # Header and filter
        header_row = ctk.CTkFrame(panel, fg_color="transparent")
        header_row.pack(fill="x", pady=(10, 15))
        
        ctk.CTkLabel(header_row, text="Downloaded Database Registry", font=ctk.CTkFont(size=24, weight="bold")).pack(side="left")
        
        self.search_entry = ctk.CTkEntry(header_row, placeholder_text="🔍 Filter history...", width=200)
        self.search_entry.pack(side="right", padx=5)
        self.search_entry.bind("<KeyRelease>", lambda e: self.filter_history_list())
        
        # Scrollable container
        self.history_scroll = ctk.CTkScrollableFrame(panel, border_width=1, border_color="gray25")
        self.history_scroll.pack(fill="both", expand=True)
        
        self.history_rows_data = [] # Stores complete historical logs for filtering

    def load_history_list(self):
        # Clear existing rows
        for child in self.history_scroll.winfo_children():
            child.destroy()
        self.history_rows_data.clear()

        if not CSV_FILE.exists():
            ctk.CTkLabel(self.history_scroll, text="No historical download logs registered.", text_color="gray60", font=ctk.CTkFont(size=14)).pack(pady=40)
            return

        try:
            import csv
            history = []
            with open(CSV_FILE, "r", encoding="utf-8") as f:
                reader = csv.DictReader(f)
                for row in reader:
                    history.append(row)
            history.reverse() # Newest first

            if not history:
                ctk.CTkLabel(self.history_scroll, text="No historical download logs registered.", text_color="gray60", font=ctk.CTkFont(size=14)).pack(pady=40)
                return

            for row in history:
                dur_sec = int(row.get("duration") or 0)
                m = dur_sec // 60
                s = dur_sec % 60
                duration_str = f"{m}m {s}s" if dur_sec else "Unknown"
                
                row_frame = ctk.CTkFrame(self.history_scroll, fg_color="transparent", border_width=1, border_color="gray20")
                row_frame.pack(fill="x", pady=4, padx=5)
                
                inner = ctk.CTkFrame(row_frame, fg_color="transparent")
                inner.pack(fill="both", expand=True, padx=15, pady=8)
                
                title_lbl = ctk.CTkLabel(
                    inner, 
                    text=row.get("title", "Unknown Video"), 
                    font=ctk.CTkFont(size=13, weight="bold"),
                    anchor="w",
                    justify="left"
                )
                title_lbl.pack(anchor="w")
                
                meta_lbl = ctk.CTkLabel(
                    inner, 
                    text=f"📺 {row.get('channel','Creator')}  |  ⏳ {duration_str}  |  🕒 Fetch Date: {row.get('fetched_at')}",
                    font=ctk.CTkFont(size=11),
                    text_color="gray60"
                )
                meta_lbl.pack(anchor="w", pady=(2,0))
                
                # Cache row frame and text details for search operations
                self.history_rows_data.append({
                    "frame": row_frame,
                    "search_str": f"{row.get('title','')} {row.get('channel','')}".lower()
                })

        except Exception as e:
            ctk.CTkLabel(self.history_scroll, text=f"Failed to load history list: {e}", text_color="gray60").pack(pady=20)

    def filter_history_list(self):
        query = self.search_entry.get().strip().lower()
        for row in self.history_rows_data:
            if query in row["search_str"]:
                row["frame"].pack(fill="x", pady=4, padx=5)
            else:
                row["frame"].pack_forget()

    # ==========================================
    # SETTINGS TAB PANEL
    # ==========================================
    def setup_settings_panel(self):
        panel = ctk.CTkFrame(self.main_container, fg_color="transparent")
        self.panels["settings"] = panel
        
        title_lbl = ctk.CTkLabel(panel, text="System Configurations", font=ctk.CTkFont(size=24, weight="bold"))
        title_lbl.pack(anchor="w", pady=(10, 2))
        
        subtitle_lbl = ctk.CTkLabel(panel, text="Manage queue worker threads and telemetry limits", text_color="gray60", font=ctk.CTkFont(size=13))
        subtitle_lbl.pack(anchor="w", pady=(0, 20))
        
        # Form Container Frame
        form_scroll = ctk.CTkScrollableFrame(panel, border_width=0, fg_color="transparent")
        form_scroll.pack(fill="both", expand=True)

        # 1. Thread Limits Section
        sec1 = ctk.CTkFrame(form_scroll)
        sec1.pack(fill="x", pady=10, padx=2)
        
        ctk.CTkLabel(sec1, text="⚡ Connection and Performance Limits", font=ctk.CTkFont(size=14, weight="bold")).pack(anchor="w", padx=20, pady=(15, 10))
        
        grid1 = ctk.CTkFrame(sec1, fg_color="transparent")
        grid1.pack(fill="x", padx=20, pady=(0, 20))
        
        ctk.CTkLabel(grid1, text="Concurrent Video Queue Size:", font=ctk.CTkFont(size=12)).grid(row=0, column=0, sticky="w", pady=5, padx=(0, 20))
        self.max_conc_entry = ctk.CTkEntry(grid1, width=80)
        self.max_conc_entry.grid(row=0, column=1, sticky="w", pady=5)
        
        ctk.CTkLabel(grid1, text="Simultaneous Connections (Aria-Segment Limit):", font=ctk.CTkFont(size=12)).grid(row=1, column=0, sticky="w", pady=5, padx=(0, 20))
        self.fragments_entry = ctk.CTkEntry(grid1, width=80)
        self.fragments_entry.grid(row=1, column=1, sticky="w", pady=5)

        # 2. Paths and Defaults Section
        sec2 = ctk.CTkFrame(form_scroll)
        sec2.pack(fill="x", pady=10, padx=2)
        
        ctk.CTkLabel(sec2, text="📂 Extraction Storage & Defaults", font=ctk.CTkFont(size=14, weight="bold")).pack(anchor="w", padx=20, pady=(15, 10))
        
        grid2 = ctk.CTkFrame(sec2, fg_color="transparent")
        grid2.pack(fill="x", padx=20, pady=(0, 20))
        
        ctk.CTkLabel(grid2, text="Download Output Root Directory:", font=ctk.CTkFont(size=12)).grid(row=0, column=0, sticky="w", pady=5, padx=(0, 20))
        path_row = ctk.CTkFrame(grid2, fg_color="transparent")
        path_row.grid(row=0, column=1, columnspan=2, sticky="ew", pady=5)
        
        self.dl_dir_var = ctk.StringVar()
        self.dl_dir_entry = ctk.CTkEntry(path_row, textvariable=self.dl_dir_var, width=320)
        self.dl_dir_entry.pack(side="left", fill="x", expand=True, padx=(0, 10))
        
        self.browse_btn = ctk.CTkButton(
            path_row, 
            text="Browse...", 
            width=80, 
            command=self.browse_download_folder
        )
        self.browse_btn.pack(side="right")
        
        ctk.CTkLabel(grid2, text="Preferred Resolution Limit:", font=ctk.CTkFont(size=12)).grid(row=1, column=0, sticky="w", pady=5, padx=(0, 20))
        self.pref_res_menu = ctk.CTkOptionMenu(grid2, values=["2160", "1440", "1080", "720", "480", "360"], width=130)
        self.pref_res_menu.grid(row=1, column=1, sticky="w", pady=5)
        
        ctk.CTkLabel(grid2, text="Preferred Format Preset:", font=ctk.CTkFont(size=12)).grid(row=2, column=0, sticky="w", pady=5, padx=(0, 20))
        self.pref_mode_menu = ctk.CTkOptionMenu(grid2, values=["video", "audio"], width=130)
        self.pref_mode_menu.grid(row=2, column=1, sticky="w", pady=5)

        # 3. Telegram Alerts Section
        sec3 = ctk.CTkFrame(form_scroll)
        sec3.pack(fill="x", pady=10, padx=2)
        
        header_tg = ctk.CTkFrame(sec3, fg_color="transparent")
        header_tg.pack(fill="x", padx=20, pady=(15, 10))
        
        ctk.CTkLabel(header_tg, text="✈️ Telegram Remote Alerts", font=ctk.CTkFont(size=14, weight="bold")).pack(side="left")
        self.tg_switch = ctk.CTkSwitch(
            header_tg, 
            text="Enabled", 
            command=self.toggle_telegram_inputs
        )
        self.tg_switch.pack(side="right")
        
        self.tg_fields_frame = ctk.CTkFrame(sec3, fg_color="transparent")
        self.tg_fields_frame.pack(fill="x", padx=20, pady=(0, 20))
        
        ctk.CTkLabel(self.tg_fields_frame, text="Telegram Bot Token:", font=ctk.CTkFont(size=12)).grid(row=0, column=0, sticky="w", pady=5, padx=(0, 20))
        self.tg_token_entry = ctk.CTkEntry(self.tg_fields_frame, width=280, show="*")
        self.tg_token_entry.grid(row=0, column=1, sticky="w", pady=5)
        
        ctk.CTkLabel(self.tg_fields_frame, text="Telegram Chat ID:", font=ctk.CTkFont(size=12)).grid(row=1, column=0, sticky="w", pady=5, padx=(0, 20))
        self.tg_chat_entry = ctk.CTkEntry(self.tg_fields_frame, width=280)
        self.tg_chat_entry.grid(row=1, column=1, sticky="w", pady=5)

        # Save actions bar
        actions = ctk.CTkFrame(panel, fg_color="transparent")
        actions.pack(fill="x", side="bottom", pady=10)
        
        save_btn = ctk.CTkButton(
            actions, 
            text="💾 Apply Configuration Settings", 
            fg_color="#10b981", 
            hover_color="#059669",
            font=ctk.CTkFont(size=14, weight="bold"),
            height=42,
            command=self.save_settings_from_form
        )
        save_btn.pack(side="right")

    def browse_download_folder(self):
        folder = filedialog.askdirectory(initialdir=self.dl_dir_var.get())
        if folder:
            self.dl_dir_var.set(folder)

    def toggle_telegram_inputs(self):
        state = self.tg_switch.get()
        # Toggle inputs widget states
        for child in self.tg_fields_frame.winfo_children():
            if isinstance(child, ctk.CTkEntry):
                child.configure(state="normal" if state == 1 else "disabled")

    def load_settings_into_form(self):
        settings = load_settings()
        self.max_conc_entry.delete(0, 'end')
        self.max_conc_entry.insert(0, str(settings["max_concurrent"]))
        
        self.fragments_entry.delete(0, 'end')
        self.fragments_entry.insert(0, str(settings["concurrent_fragments"]))
        
        self.dl_dir_var.set(settings["download_dir"])
        self.pref_res_menu.set(settings["default_resolution"])
        self.pref_mode_menu.set(settings["default_mode"])
        
        if settings["telegram_enabled"]:
            self.tg_switch.select()
        else:
            self.tg_switch.deselect()
            
        self.tg_token_entry.delete(0, 'end')
        self.tg_token_entry.insert(0, settings["telegram_bot_token"])
        
        self.tg_chat_entry.delete(0, 'end')
        self.tg_chat_entry.insert(0, settings["telegram_chat_id"])
        
        self.toggle_telegram_inputs()

    def save_settings_from_form(self):
        try:
            payload = {
                "max_concurrent": max(1, int(self.max_conc_entry.get().strip())),
                "concurrent_fragments": max(1, int(self.fragments_entry.get().strip())),
                "download_dir": self.dl_dir_var.get().strip(),
                "default_resolution": self.pref_res_menu.get(),
                "default_mode": self.pref_mode_menu.get(),
                "telegram_enabled": bool(self.tg_switch.get()),
                "telegram_bot_token": self.tg_token_entry.get().strip(),
                "telegram_chat_id": self.tg_chat_entry.get().strip()
            }
            
            if not payload["download_dir"]:
                messagebox.showerror("Settings Error", "Download output root folder path cannot be empty.")
                return
                
            if save_settings(payload):
                self.concurrency_lbl.configure(text=f"Max Concurrent: {payload['max_concurrent']}")
                with queue_condition:
                    queue_condition.notify_all()
                messagebox.showinfo("Settings Panel", "Configuration settings successfully applied and locked.")
                self.refresh_quota_status()
        except ValueError:
            messagebox.showerror("Settings Error", "Max concurrent and segment connections fields must be valid numeric values.")

    # ==========================================
    # SYSTEM GAUGE & DYNAMIC UPDATERS
    # ==========================================
    def refresh_quota_status(self):
        # Reads daily state and updates dashboard stats widgets
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
        
        # Calculate visual ratio
        ratio = min(1.0, downloaded_today / daily_quota)
        self.quota_pb.set(ratio)
        
        # Set quota color accents
        if ratio >= 1.0:
            self.quota_pb.configure(progress_color="#ef4444")
            self.quota_desc.configure(text=f"⛔ Quota Limit Depleted: {downloaded_today} / {daily_quota} used. Allowance resets tomorrow.", text_color="#ef4444")
        elif ratio >= 0.8:
            self.quota_pb.configure(progress_color="#f59e0b")
            self.quota_desc.configure(text=f"⚠️ Approaching Limit: {downloaded_today} / {daily_quota} downloaded. Please conserve allocations.", text_color="#f59e0b")
        else:
            self.quota_pb.configure(progress_color="#00f2fe")
            self.quota_desc.configure(text=f"✅ Optimal Status: {downloaded_today} / {daily_quota} automated allowance slots downloaded today.", text_color="gray60")

    def poll_gui_updates(self):
        # Read download progress and update sliders/status lists
        if self.panels["queue"].winfo_viewable():
            self.rebuild_queue_gui()
            
        # Periodically refresh daily state metrics on dashboard
        if self.panels["dashboard"].winfo_viewable():
            self.refresh_quota_status()
            
        # Re-run after 200 milliseconds (thread-safe timer loop)
        self.after(200, self.poll_gui_updates)

# ==========================================
# APP ENTRY TRIGGER
# ==========================================
if __name__ == "__main__":
    app = AntigravityIDMApp()
    app.mainloop()
