import yt_dlp
import os
import re

# =========================
# VTT → TXT CONVERTER
# =========================
def convert_vtt_to_txt(vtt_path):
    import re
    import os

    base, _ = os.path.splitext(vtt_path)
    txt_path = base + ".txt"

    with open(vtt_path, "r", encoding="utf-8") as f:
        lines = f.readlines()

    # Patterns
    timestamp_pattern = re.compile(
        r"\d{2}:\d{2}:\d{2}\.\d{3}\s*-->\s*\d{2}:\d{2}:\d{2}\.\d{3}"
    )
    tag_pattern = re.compile(r"<.*?>")  # remove HTML tags

    paragraphs = []
    current_paragraph = []
    last_line = ""

    for line in lines:
        line = line.strip()

        # Paragraph break
        if not line:
            if current_paragraph:
                paragraphs.append(" ".join(current_paragraph))
                current_paragraph = []
            continue

        # Skip metadata
        if line.upper().startswith("WEBVTT") or line.startswith("NOTE"):
            continue

        # Skip timestamps
        if timestamp_pattern.search(line):
            continue

        # Remove HTML tags
        line = re.sub(tag_pattern, "", line)

        # Remove speaker labels like: "Speaker: text"
        if ":" in line and len(line.split(":")[0]) < 25:
            parts = line.split(":", 1)
            if parts[0].isupper() or parts[0].istitle():
                line = parts[1].strip()

        # Remove duplicate consecutive lines
        if line == last_line:
            continue

        last_line = line

        # Add cleaned line
        current_paragraph.append(line)

    # Final paragraph
    if current_paragraph:
        paragraphs.append(" ".join(current_paragraph))

    # Write output
    with open(txt_path, "w", encoding="utf-8") as f:
        f.write("\n\n".join(paragraphs))

    print(f"Converted: {txt_path}")
        
# =========================
# DOWNLOAD SUBTITLES ONLY
# =========================
def download_subtitles(url):
    ydl_opts = {
        'skip_download': True,  # 🚀 No video download
        'writesubtitles': True,
        'writeautomaticsub': True,
        'subtitleslangs': ['en'],
        'outtmpl': 'downloads/%(playlist_title)s/%(title)s.%(ext)s',
        'ignoreerrors': True,
        'quiet': False
    }

    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        ydl.download([url])


# =========================
# CONVERT ALL VTT IN FOLDER
# =========================
def convert_folder(folder_path):
    for root, _, files in os.walk(folder_path):
        for file in files:
            if file.endswith(".vtt"):
                full_path = os.path.join(root, file)
                convert_vtt_to_txt(full_path)


# =========================
# MAIN CONTROL
# =========================
def main():
    print("\nSelect Mode:")
    print("1 → Download subtitles + convert")
    print("2 → Convert existing folder")

    choice = input("Enter choice (1/2): ").strip()

    if choice == "1":
        url = input("Enter Video/Playlist URL: ").strip()
        download_subtitles(url)

        print("\nNow converting downloaded subtitles...\n")
        convert_folder("downloads")

    elif choice == "2":
        folder = input("Enter folder path (or press Enter for current): ").strip()

        if not folder:
            folder = os.getcwd()

        if not os.path.exists(folder):
            print("Invalid folder path.")
            return

        convert_folder(folder)

    else:
        print("Invalid choice.")


# =========================
# RUN
# =========================
if __name__ == "__main__":
    main()
