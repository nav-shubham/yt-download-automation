# YouTube Download Automation Suite

A powerful Python-based YouTube automation toolkit built using `yt-dlp`.

This project supports:
- Bulk playlist downloading
- Video/audio modes
- Automatic subtitle downloading
- Subtitle conversion (VTT → TXT)
- Telegram notifications
- Daily download limits
- Metadata extraction to CSV
- Download tracking system
- Resume-safe downloads

---

# Features

## 1. Smart Video Downloader
Downloads:
- Single videos
- Entire playlists
- Audio-only mode
- Custom resolutions

Supports:
- 360p / 480p / 720p / 1080p
- Automatic merging
- Retry system
- Archive tracking

Main file:
```bash
auto_downloader.py
```

---

## 2. Subtitle Downloader + Converter

Downloads subtitles and converts:
```text
.vtt → .txt
```

Features:
- Automatic subtitle extraction
- Cleans timestamps
- Removes duplicate lines
- Creates readable paragraphs

Main file:
```bash
subtitles.py
```

---

## 3. Metadata Extractor

Extracts video metadata without YouTube API.

Exports:
- Video title
- Channel
- Views
- Duration
- Upload date
- URL

CSV Output:
```text
youtube_no_api.csv
```

Main file:
```bash
downloaded.py
```

---

# Project Structure

```text
yt_download/
│
├── downloads/
├── old versions/
├── auto_downloader.py
├── subtitles.py
├── downloaded.py
├── yt_download.py
├── requirements.txt
├── .env
├── .gitignore
└── README.md
```

---

# Installation

## Clone Repository

```bash
git clone https://github.com/YOUR_USERNAME/yt-download-automation.git
cd yt-download-automation
```

---

## Create Virtual Environment

```bash
python -m venv venv
```

Activate:

### Windows
```bash
venv\Scripts\activate
```

### Linux / Mac
```bash
source venv/bin/activate
```

---

## Install Dependencies

```bash
pip install -r requirements.txt
```

---

# Environment Variables

Create `.env`

```env
TELEGRAM_BOT_TOKEN=your_token
TELEGRAM_CHAT_ID=your_chat_id
```

---

# Usage

## Run Main Downloader

```bash
python auto_downloader.py
```

---

## Run Subtitle Tool

```bash
python subtitles.py
```

---

## Run Metadata Extractor

```bash
python downloaded.py
```

---

# Technologies Used

- Python
- yt-dlp
- requests
- python-dotenv

---

# Notes

- Cookies supported for restricted videos
- Download archive prevents duplicates
- Safe for long playlist automation
- Telegram notifications supported

---

# License

Personal / Educational Use
