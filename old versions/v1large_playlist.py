import yt_dlp
import os
from pathlib import Path

def get_user_input():
    url = input("Enter Video/Playlist URL: ").strip()

    while True:
        mode = input("Mode (video/audio): ").strip().lower()
        if mode in ["video", "audio"]:
            break
        print("Invalid mode. Choose 'video' or 'audio'.")

    resolution = None
    if mode == "video":
        while True:
            resolution = input("Resolution (360/480/720/1080): ").strip()
            if resolution in ["360", "480", "720", "1080"]:
                break
            print("Invalid resolution.")

    return url, mode, resolution


def build_format(mode, resolution):
    if mode == "audio":
        return "bestaudio[ext=m4a]/bestaudio"
    else:
        return f"bestvideo[height<={resolution}][ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]"


def progress_hook(d):
    if d['status'] == 'downloading':
        percent = d.get('_percent_str', '').strip()
        speed = d.get('_speed_str', '').strip()
        eta = d.get('_eta_str', '').strip()
        print(f"\r📥 {percent} | ⚡ {speed} | ⏳ {eta}", end='')

    elif d['status'] == 'finished':
        print("\n✅ Download complete, processing...")


def main():
    url, mode, resolution = get_user_input()
    format_option = build_format(mode, resolution)

    Path("downloads").mkdir(exist_ok=True)

    ydl_opts = {
        'format': format_option,

        # KEEP SAME TEMPLATE → ensures resume compatibility
        'outtmpl': 'downloads/%(playlist_title)s/%(playlist_index)s - %(title)s.%(ext)s',

        'merge_output_format': 'mp4',

        # Resume + archive (IMPORTANT)
        'continuedl': True,
        'download_archive': 'downloaded.txt',

        # Stability
        'ignoreerrors': True,
        'retries': 10,
        'fragment_retries': 10,
        'skip_unavailable_fragments': True,

        # Rate limiting
        'sleep_interval': 3,
        'max_sleep_interval': 10,

        # Speed
        'concurrent_fragment_downloads': 5,

        # UX improvements
        'progress_hooks': [progress_hook],
        'noprogress': True,
        'quiet': True,

        # Advanced compatibility
        'js_runtimes': {'node': {}},
        'remote_components': ['ejs:github'],
    }

    print("\n🚀 Starting download...\n")

    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        ydl.download([url])

    print("\n🎉 All done.")


if __name__ == "__main__":
    main()
