# /// script
# requires-python = ">=3.10"
# dependencies = [
#     "requests",
#     "openpyxl",
#     "python-dotenv",
# ]
# ///
"""
YouTube Channel Video Lister
=============================

Fetches every video from a YouTube channel (title, URL, publish date, views,
likes, duration) and writes them to an Excel file.

SETUP
-----
1. Get a YouTube Data API v3 key:
   https://console.cloud.google.com/apis/credentials
   (enable "YouTube Data API v3" on the project first, if you haven't)

2. Create a file named ".env" in the same folder as this script, containing:
       YOUTUBE_API_KEY=your_key_here

   (No quotes needed, no "export" - just KEY=value. Keep this file out of
   version control, e.g. add ".env" to your .gitignore.)

USAGE
-----
    uv run youtube_video_lister.py <channel>
    uv run youtube_video_lister.py <channel> -o my_videos.xlsx

    <channel> can be:
        - a channel URL   e.g. https://www.youtube.com/@MrBeast
        - a handle        e.g. @MrBeast
        - a channel ID    e.g. UCX6OQ3DkcsbYNE6H8uQQuVA

    Without uv:
        pip install requests openpyxl
        python youtube_video_lister.py <channel>
"""

import argparse
import os
import re
import sys

import requests
from dotenv import load_dotenv
from openpyxl import Workbook
from openpyxl.styles import Font, Alignment
from openpyxl.utils import get_column_letter

API_BASE = "https://www.googleapis.com/youtube/v3"
CHANNEL_ID_RE = re.compile(r"UC[\w-]{22}")


def get_api_key() -> str:
    load_dotenv()  # reads YOUTUBE_API_KEY from a .env file next to this script
    key = os.environ.get("YOUTUBE_API_KEY")
    if not key:
        sys.exit(
            "ERROR: YOUTUBE_API_KEY not found.\n"
            "Create a .env file next to this script containing:\n"
            "  YOUTUBE_API_KEY=your_key_here"
        )
    return key


def api_get(endpoint: str, params: dict) -> dict:
    resp = requests.get(f"{API_BASE}/{endpoint}", params=params, timeout=30)
    if resp.status_code != 200:
        try:
            message = resp.json()["error"]["message"]
        except Exception:
            message = resp.text
        sys.exit(f"ERROR calling YouTube API ({resp.status_code}): {message}")
    return resp.json()


def resolve_channel_id(channel_input: str, api_key: str) -> str:
    """Turn a URL, @handle, or channel ID into a channel ID."""
    value = channel_input.strip()

    if CHANNEL_ID_RE.fullmatch(value):
        return value

    # Pull the meaningful part out of a full URL, if one was given
    match = re.search(r"youtube\.com/(?:channel/|c/|user/|@)?([^/?&]+)", value)
    handle_or_name = (match.group(1) if match else value).lstrip("@")

    if CHANNEL_ID_RE.fullmatch(handle_or_name):
        return handle_or_name

    # Try as a handle first - works for virtually all modern channels
    data = api_get("channels", {"part": "id", "forHandle": handle_or_name, "key": api_key})
    if data.get("items"):
        return data["items"][0]["id"]

    # Fall back to legacy username lookup
    data = api_get("channels", {"part": "id", "forUsername": handle_or_name, "key": api_key})
    if data.get("items"):
        return data["items"][0]["id"]

    # Last resort: search by name (costs more quota, but catches old /c/ URLs)
    data = api_get("search", {
        "part": "snippet", "q": handle_or_name, "type": "channel",
        "maxResults": 1, "key": api_key,
    })
    if data.get("items"):
        return data["items"][0]["snippet"]["channelId"]

    sys.exit(f"ERROR: Could not find a channel matching '{channel_input}'.")


def get_uploads_playlist_id(channel_id: str, api_key: str) -> tuple[str, str]:
    data = api_get("channels", {"part": "contentDetails,snippet", "id": channel_id, "key": api_key})
    items = data.get("items", [])
    if not items:
        sys.exit(f"ERROR: Channel ID '{channel_id}' not found.")
    playlist_id = items[0]["contentDetails"]["relatedPlaylists"]["uploads"]
    channel_title = items[0]["snippet"]["title"]
    return playlist_id, channel_title


def get_all_video_ids(playlist_id: str, api_key: str) -> list[str]:
    video_ids = []
    page_token = None
    while True:
        params = {"part": "contentDetails", "playlistId": playlist_id, "maxResults": 50, "key": api_key}
        if page_token:
            params["pageToken"] = page_token
        data = api_get("playlistItems", params)
        video_ids.extend(item["contentDetails"]["videoId"] for item in data.get("items", []))
        print(f"  Found {len(video_ids)} videos so far...")
        page_token = data.get("nextPageToken")
        if not page_token:
            break
    return video_ids


def parse_duration(iso_duration: str) -> str:
    """Convert ISO 8601 duration (e.g. PT1H2M10S) to H:MM:SS or MM:SS."""
    match = re.fullmatch(r"PT(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?", iso_duration)
    if not match:
        return iso_duration
    hours, minutes, seconds = (int(g) if g else 0 for g in match.groups())
    if hours:
        return f"{hours}:{minutes:02d}:{seconds:02d}"
    return f"{minutes}:{seconds:02d}"


def get_video_details(video_ids: list[str], api_key: str) -> list[dict]:
    videos = []
    for i in range(0, len(video_ids), 50):
        batch = video_ids[i:i + 50]
        data = api_get("videos", {"part": "snippet,contentDetails,statistics", "id": ",".join(batch), "key": api_key})
        for item in data.get("items", []):
            snippet = item["snippet"]
            stats = item.get("statistics", {})
            content = item["contentDetails"]
            videos.append({
                "title": snippet["title"],
                "url": f"https://www.youtube.com/watch?v={item['id']}",
                "published": snippet["publishedAt"][:10],
                "views": int(stats.get("viewCount", 0)),
                "likes": int(stats["likeCount"]) if "likeCount" in stats else "N/A",
                "duration": parse_duration(content["duration"]),
            })
        print(f"  Fetched details for {min(i + 50, len(video_ids))}/{len(video_ids)} videos...")
    return videos


def write_excel(videos: list[dict], channel_title: str, output_path: str):
    videos.sort(key=lambda v: v["published"], reverse=True)

    wb = Workbook()
    ws = wb.active
    ws.title = "Videos"

    headers = ["Title", "URL", "Published", "Views", "Likes", "Duration"]
    ws.append(headers)
    for col in range(1, len(headers) + 1):
        cell = ws.cell(row=1, column=col)
        cell.font = Font(bold=True)
        cell.alignment = Alignment(horizontal="center")

    for row_idx, v in enumerate(videos, start=2):
        ws.append([v["title"], v["url"], v["published"], v["views"], v["likes"], v["duration"]])
        ws.cell(row=row_idx, column=4).number_format = "#,##0"
        if isinstance(v["likes"], int):
            ws.cell(row=row_idx, column=5).number_format = "#,##0"

    for col, width in enumerate([60, 45, 12, 10, 10, 10], start=1):
        ws.column_dimensions[get_column_letter(col)].width = width

    ws.freeze_panes = "A2"
    wb.save(output_path)
    print(f"\nSaved {len(videos)} videos from '{channel_title}' to {output_path}")


def main():
    parser = argparse.ArgumentParser(description="List all videos of a YouTube channel into an Excel file.")
    parser.add_argument("channel", help="Channel URL, @handle, or channel ID")
    parser.add_argument("-o", "--output", default=None, help="Output .xlsx filename")
    args = parser.parse_args()

    api_key = get_api_key()

    print("Resolving channel...")
    channel_id = resolve_channel_id(args.channel, api_key)

    print("Getting uploads playlist...")
    playlist_id, channel_title = get_uploads_playlist_id(channel_id, api_key)
    print(f"Channel found: {channel_title}")

    print("Listing all videos (this may take a while for large channels)...")
    video_ids = get_all_video_ids(playlist_id, api_key)

    print("Fetching video details (views, likes, duration)...")
    videos = get_video_details(video_ids, api_key)

    output_path = args.output or f"{re.sub(r'[^\w\-]+', '_', channel_title)}_videos.xlsx"
    write_excel(videos, channel_title, output_path)


if __name__ == "__main__":
    main()
