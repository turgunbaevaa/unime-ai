import os
import subprocess

from db import get_videos_collection
from video_schema import build_imported_video

# --- Configuration ---
BASE_DIR = r"C:\Users\ciam\unime.it\Part-Time Multimedia - Backup_CD"
TXT_FILE = os.path.join(BASE_DIR, "Elenco video.txt")
OUTPUT_DIR = r"C:\Users\ciam\unime-ai\converted_videos"


def process_archive():
    print("🔍 Starting archive batch processing...\n")

    videos_collection = get_videos_collection()
    os.makedirs(OUTPUT_DIR, exist_ok=True)

    with open(TXT_FILE, 'r', encoding='utf-8') as file:
        lines = file.readlines()

    for line in lines:
        if not line.strip() or line.startswith("!"):
            continue

        parts = line.split(";")
        if len(parts) >= 3:
            raw_folder_name = parts[0].strip()
            topic = parts[1].strip()
            participants = parts[2].strip()

            # 2. Idempotency Check: Skip if already processed
            existing_video = videos_collection.find_one({"original_folder": raw_folder_name})
            if existing_video:
                print(f"⏭️ Skipping (already in DB): {topic}")
                continue

            found_path = None
            for root, dirs, files in os.walk(BASE_DIR):
                if raw_folder_name in dirs:
                    found_path = os.path.join(root, raw_folder_name)
                    break

            if found_path:
                print(f"\n✅ Processing: {topic}")

                vob_files = [f for f in os.listdir(found_path) if f.endswith('.VOB')]
                vob_files.sort()

                if vob_files:
                    concat_string = "concat:" + "|".join(vob_files)
                    output_filename = f"{raw_folder_name}.mp4"
                    output_filepath = os.path.join(OUTPUT_DIR, output_filename)

                    ffmpeg_cmd = [
                        "ffmpeg", "-y", "-i", concat_string,
                        "-c:v", "libx264", "-preset", "fast", "-crf", "28",
                        "-c:a", "aac", output_filepath
                    ]

                    print("⏳ Encoding to MP4... (Silencing FFmpeg output to keep terminal clean)")
                    result = subprocess.run(
                        ffmpeg_cmd,
                        cwd=found_path,
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                    )
                    if result.returncode != 0 or not os.path.exists(output_filepath) or os.path.getsize(output_filepath) == 0:
                        print(f"❌ Encoding failed for '{topic}' (return code {result.returncode}). Skipping insert.")
                        continue

                    print("💾 Saving metadata to MongoDB...")
                    video_doc = build_imported_video(
                        title=topic,
                        original_folder=raw_folder_name,
                        participants=participants,
                        media_path=output_filepath,
                    )
                    videos_collection.insert_one(video_doc)
                    print(f"🎉 Success! Database updated (status=pending).")

if __name__ == "__main__":
    process_archive()
