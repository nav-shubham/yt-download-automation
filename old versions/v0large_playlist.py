import yt_dlp

url = input("Enter Video/Playlist URL: ")
mode = input("Enter mode (video/audio): ")
resolution = input("Enter resolution (360/480/720/1080): ")

if mode == "audio":
    format_option = "bestaudio[ext=m4a]/bestaudio"
else:
    format_option = f'bestvideo[height<={resolution}][ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]'

ydl_opts = {
    'format': format_option,
    'outtmpl': 'downloads/%(playlist_title)s/%(playlist_index)s - %(title)s.%(ext)s',
    'merge_output_format': 'mp4',
    'js_runtimes': {'node': {}},
    'remote_components': ['ejs:github'],

    # Important for large playlists
    'ignoreerrors': True,
    'retries': 10,
    'fragment_retries': 10,
    'skip_unavailable_fragments': True,
    'continuedl': True,
    'noplaylist': False,
    'download_archive': 'downloaded.txt',

    # Avoid rate limiting
    'sleep_interval': 5,
    'max_sleep_interval': 15,

    'concurrent_fragment_downloads': 3,
    'no_warnings': True
}

with yt_dlp.YoutubeDL(ydl_opts) as ydl:
    ydl.download([url])