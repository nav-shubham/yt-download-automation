import yt_dlp
from pathlib import Path
url = input("Enter Video/Playlist URL: ")
mode = input("Enter mode (video/audio): ")
resolution = input("Enter resolution (360/480/720/1080): ")
COOKIE_FILE = Path("cookies.txt")
if mode == "audio":
    format_option = "bestaudio[ext=m4a]/bestaudio"
else:
    format_option = f'bestvideo[height<={resolution}][ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]'

ydl_opts = {
    'format': format_option,
    'outtmpl': 'downloads/%(playlist_title)s/%(title)s.%(ext)s',
    'merge_output_format': 'mp4',
    'js_runtimes': {'node': {}},
    'remote_components': ['ejs:github'],
    "cookiefile": str(COOKIE_FILE),
    'writesubtitles': True,
    'writeautomaticsub': True,
    'subtitleslangs': ['en'],
    'sleep_interval': 3,
    'max_sleep_interval': 6,
    'ignoreerrors': True,
    'no_warnings': True
}

with yt_dlp.YoutubeDL(ydl_opts) as ydl:
    ydl.download([url])
