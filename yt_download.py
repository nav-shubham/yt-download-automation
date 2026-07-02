import yt_dlp
from pathlib import Path
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
import sys

COOKIE_FILE = Path("cookies.txt")
ARCHIVE_FILE = Path("downloaded.txt")
print_lock = threading.Lock()

def extract_video_urls(url):
    """Extract individual video URLs from a playlist or single video URL."""
    print(f"[*] Extracting information from: {url}")
    ydl_opts = {
        'extract_flat': True,
        'skip_download': True,
        'cookiefile': str(COOKIE_FILE) if COOKIE_FILE.exists() else None,
        'ignoreerrors': True,
        'no_warnings': True,
        'quiet': True
    }
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        try:
            info = ydl.extract_info(url, download=False)
            if info is None:
                return [url]
            if 'entries' in info:
                # It's a playlist or channel
                urls = []
                for entry in info['entries']:
                    if entry:
                        # Extract the video URL
                        video_url = entry.get('url') or entry.get('webpage_url')
                        if video_url:
                            # If it's a short URL / ID, format it
                            if not video_url.startswith('http'):
                                video_url = f"https://www.youtube.com/watch?v={video_url}"
                            urls.append(video_url)
                return urls
            else:
                # Single video
                return [url]
        except Exception as e:
            print(f"[!] Error extracting playlist info: {e}")
            return [url]

def download_single_video(url, mode, resolution, prefix=""):
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
        'writesubtitles': True,
        'writeautomaticsub': True,
        'subtitleslangs': ['en'],
        "cookiefile": str(COOKIE_FILE) if COOKIE_FILE.exists() else None,
        "download_archive": str(ARCHIVE_FILE),
        'ignoreerrors': True,
        'no_warnings': True,
        'quiet': True,
        'noprogress': True
    }
    
    # Try to extract video title for nice logging
    title = url
    try:
        with yt_dlp.YoutubeDL({'quiet': True, 'cookiefile': ydl_opts['cookiefile']}) as ydl:
            info = ydl.extract_info(url, download=False)
            if info:
                title = info.get('title', url)
    except Exception:
        pass

    with print_lock:
        print(f"{prefix}[*] Starting download: {title}")
        sys.stdout.flush()
        
    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            ydl.download([url])
        with print_lock:
            print(f"{prefix}[+] Finished download: {title}")
            sys.stdout.flush()
        return True
    except Exception as e:
        with print_lock:
            print(f"{prefix}[Error] Failed downloading {title}: {e}")
            sys.stdout.flush()
        return False

def main():
    print("==========================================================")
    print("   YouTube Downloader Script (Multi-threaded & Playlist)")
    print("==========================================================")
    
    url_input = input("Enter Video/Playlist URL(s) (comma-separated if multiple): ").strip()
    if not url_input:
        print("[Error] URL is required.")
        return
        
    mode = input("Enter mode (video/audio, default: video): ").strip().lower()
    if mode not in ["video", "audio"]:
        mode = "video"
        
    resolution = "1080"
    if mode == "video":
        resolution_input = input("Enter resolution (360/480/720/1080, default: 1080): ").strip()
        if resolution_input in ["360", "480", "720", "1080"]:
            resolution = resolution_input

    threads_input = input("Enter number of concurrent download threads (default: 4): ").strip()
    if threads_input.isdigit():
        threads = int(threads_input)
    else:
        threads = 4

    # Parse and extract URLs
    raw_urls = [u.strip() for u in url_input.split(",") if u.strip()]
    video_urls = []
    
    for r_url in raw_urls:
        video_urls.extend(extract_video_urls(r_url))
        
    # Remove duplicates while preserving order
    seen = set()
    video_urls = [x for x in video_urls if not (x in seen or seen.add(x))]
    
    total_videos = len(video_urls)
    if total_videos == 0:
        print("[!] No video URLs found to download.")
        return
        
    print(f"\n[+] Found {total_videos} video(s) to download.")
    print(f"[*] Downloading with {threads} threads...\n")

    completed_tasks = 0
    completed_lock = threading.Lock()
    
    def worker(video_url):
        nonlocal completed_tasks
        with completed_lock:
            completed_tasks += 1
            task_id = completed_tasks
        prefix = f"[{task_id}/{total_videos}] "
        download_single_video(video_url, mode, resolution, prefix=prefix)

    # Run ThreadPoolExecutor
    if threads <= 1 or total_videos == 1:
        # Sequential download
        for idx, video_url in enumerate(video_urls, 1):
            prefix = f"[{idx}/{total_videos}] "
            download_single_video(video_url, mode, resolution, prefix=prefix)
    else:
        # Multi-threaded download
        with ThreadPoolExecutor(max_workers=threads) as executor:
            futures = [executor.submit(worker, url) for url in video_urls]
            for future in as_completed(futures):
                try:
                    future.result()
                except Exception as e:
                    print(f"[!] Worker generated an exception: {e}")
                    
    print("\n==========================================================")
    print("   Downloads Finished!")
    print("==========================================================")

if __name__ == "__main__":
    main()
