# YouTube Download Automation

Features:
- Playlist/video downloader
- Subtitle downloader
- Subtitle conversion (VTT → TXT)
- Telegram notifications
- Daily download quota
- CSV metadata extraction
- yt-dlp based automation

## Setup

```bash
pip install -r requirements.txt
```

Create `.env`

```env
TELEGRAM_BOT_TOKEN=your_token
TELEGRAM_CHAT_ID=your_chat_id
```

Run:

```bash
python auto_downloader.py
```
