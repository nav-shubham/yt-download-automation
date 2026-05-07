import yt_dlp
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
        # Fast and simple audio download
        return "bestaudio[ext=m4a]/bestaudio/best"
    else:
        # Prefer a single MP4 when possible, avoid unnecessary merging
        return f"best[ext=mp4][height<={resolution}]/best[height<={resolution}]/best[ext=mp4]/best"


def progress_hook(d):
    if d["status"] == "downloading":
        percent = d.get("_percent_str", "").strip()
        speed = d.get("_speed_str", "").strip()
        eta = d.get("_eta_str", "").strip()
        print(f"\r📥 {percent} | ⚡ {speed} | ⏳ {eta}", end="", flush=True)

    elif d["status"] == "finished":
        print("\n✅ Download complete, processing...")


def main():
    url, mode, resolution = get_user_input()
    format_option = build_format(mode, resolution)

    Path("downloads").mkdir(exist_ok=True)

    ydl_opts = {
        "format": format_option,

        # Keep playlist organization
        "outtmpl": "downloads/%(playlist_title)s/%(playlist_index)s - %(title)s.%(ext)s",
        "paths": {"home": "downloads"},

        # Merge only if yt-dlp ends up needing separate streams
        "merge_output_format": "mp4",

        # Resume support
        "continuedl": True,
        "nopart": False,
        "download_archive": "downloaded.txt",

        # Reliability
        "ignoreerrors": True,
        "retries": 10,
        "fragment_retries": 10,
        "skip_unavailable_fragments": True,

        # Keep your sleep time
        "sleep_interval": 3,
        "max_sleep_interval": 10,

        # Faster when fragments are used
        "concurrent_fragment_downloads": 10,

        # Clean output
        "progress_hooks": [progress_hook],
        "noprogress": True,
        "quiet": True,
        "no_warnings": True,
    }

    print("\n🚀 Starting download...\n")

    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        ydl.download([url])

    print("\n🎉 All done.")


if __name__ == "__main__":
    main()
