import yt_dlp
import csv
import re
import os
import time
from datetime import datetime

INPUT_FILE = "downloaded.txt"
OUTPUT_FILE = "youtube_no_api.csv"
ERROR_LOG = "errors.log"

# ==============================
# Extract video IDs
# ==============================
def extract_video_ids(file_path):
    ids = []
    with open(file_path, "r", encoding="utf-8") as f:
        for line in f:
            match = re.search(r"youtube\s+([A-Za-z0-9_-]{11})", line)
            if match:
                ids.append(match.group(1))
    return list(set(ids))


# ==============================
# Load existing IDs
# ==============================
def load_existing_ids(file_path):
    if not os.path.exists(file_path):
        print("⚠️ No existing database found")
        return set()

    existing_ids = set()

    with open(file_path, "r", encoding="utf-8") as f:
        reader = csv.DictReader(f)

        if "video_id" not in reader.fieldnames:
            print("❌ 'video_id' column missing in CSV")
            return set()

        for row in reader:
            vid = row.get("video_id")
            if vid:
                existing_ids.add(vid.strip())

    print(f"✅ Loaded {len(existing_ids)} existing IDs")
    return existing_ids


# ==============================
# Log errors
# ==============================
def log_error(video_id, error_msg):
    with open(ERROR_LOG, "a", encoding="utf-8") as f:
        f.write(f"{datetime.now()} | {video_id} | {error_msg}\n")


# ==============================
# Append ONE row instantly
# ==============================
def append_row(data, file_path):
    file_exists = os.path.exists(file_path)

    with open(file_path, "a", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=data.keys())

        if not file_exists:
            writer.writeheader()

        writer.writerow(data)


# ==============================
# Fetch + SAVE immediately
# ==============================
def process_videos(video_ids, existing_ids):
    ydl_opts = {
        'quiet': True,
        'skip_download': True,
        'js_runtimes': {'node': {}},  # correct format
        'extractor_args': {
            'youtube': {
                'player_client': ['web']
            }
        }
    }

    processed = 0

    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        for vid in video_ids:

            if vid in existing_ids:
                continue  # skip already done

            url = f"https://www.youtube.com/watch?v={vid}"

            for attempt in range(3):
                try:
                    info = ydl.extract_info(url, download=False)

                    row = {
                        "video_id": vid,
                        "title": info.get("title"),
                        "channel": info.get("uploader"),
                        "views": info.get("view_count"),
                        "duration": info.get("duration"),
                        "upload_date": info.get("upload_date"),
                        "url": url,
                        "fetched_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                    }

                    # SAVE IMMEDIATELY (critical fix)
                    append_row(row, OUTPUT_FILE)

                    print(f"✅ {vid}")
                    processed += 1
                    break

                except Exception as e:
                    if attempt == 2:
                        print(f"❌ Failed: {vid}")
                        log_error(vid, str(e))
                    else:
                        time.sleep(2)

    return processed


# ==============================
# MAIN
# ==============================
if __name__ == "__main__":
    print("Reading input file...")
    all_ids = extract_video_ids(INPUT_FILE)

    print("Loading existing database...")
    existing_ids = load_existing_ids(OUTPUT_FILE)

    print(f"Total IDs: {len(all_ids)}")
    print(f"Already processed: {len(existing_ids)}")

    print("Processing only NEW videos...")
    processed_count = process_videos(all_ids, existing_ids)

    print(f"✅ New videos added: {processed_count}")
    print("📄 Errors logged in:", ERROR_LOG)
