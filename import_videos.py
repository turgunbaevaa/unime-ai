import os
import subprocess

import config
from db import get_folders_collection, get_videos_collection
from video_schema import build_folder, build_imported_video


def _list_file():
    if config.IMPORT_LIST_FILE:
        return config.IMPORT_LIST_FILE
    return os.path.join(config.IMPORT_BASE_DIR, "Elenco video.txt")


def _get_or_create_folder(folders_collection, raw_folder_name, topic, participants):
    existing = folders_collection.find_one({"name": raw_folder_name})
    if existing:
        return existing["_id"]
    result = folders_collection.insert_one(
        build_folder(raw_folder_name, topic, participants)
    )
    return result.inserted_id


def process_archive():
    base_dir = config.IMPORT_BASE_DIR
    txt_file = _list_file()
    output_dir = config.IMPORT_OUTPUT_DIR

    print("🔍 Starting archive batch processing...\n")
    print(f"   Source list: {txt_file}")
    print(f"   DVD root:    {base_dir}")
    print(f"   MP4 output:  {output_dir}")
    if config.IMPORT_MAX_VIDEOS:
        print(f"   Limit:       {config.IMPORT_MAX_VIDEOS} video(s) (IMPORT_MAX_VIDEOS)\n")
    else:
        print()

    if not os.path.isfile(txt_file):
        print(f"❌ List file not found: {txt_file}")
        print("   Set IMPORT_LIST_FILE or IMPORT_BASE_DIR in .env")
        return

    videos_collection = get_videos_collection()
    folders_collection = get_folders_collection()
    os.makedirs(output_dir, exist_ok=True)

    imported = 0
    skipped = 0
    failed = 0

    with open(txt_file, "r", encoding="utf-8") as file:
        lines = file.readlines()

    for line in lines:
        if config.IMPORT_MAX_VIDEOS and imported >= config.IMPORT_MAX_VIDEOS:
            print(f"\n⏹️ Reached IMPORT_MAX_VIDEOS={config.IMPORT_MAX_VIDEOS}. Stopping.")
            break

        if not line.strip() or line.startswith("!"):
            continue

        parts = line.split(";")
        if len(parts) < 3:
            continue

        raw_folder_name = parts[0].strip()
        topic = parts[1].strip()
        participants = parts[2].strip()

        if videos_collection.find_one({"original_folder": raw_folder_name}):
            print(f"⏭️ Skipping (already in DB): {topic}")
            skipped += 1
            continue

        found_path = None
        for root, dirs, files in os.walk(base_dir):
            if raw_folder_name in dirs:
                found_path = os.path.join(root, raw_folder_name)
                break

        if not found_path:
            print(f"❌ Folder not found on disk: {raw_folder_name}")
            failed += 1
            continue

        vob_files = [f for f in os.listdir(found_path) if f.endswith(".VOB")]
        vob_files.sort()
        if not vob_files:
            print(f"❌ No .VOB files in: {found_path}")
            failed += 1
            continue

        print(f"\n✅ Processing: {topic}")

        output_filename = f"{raw_folder_name}.mp4"
        output_filepath = os.path.join(output_dir, output_filename)

        if os.path.isfile(output_filepath) and os.path.getsize(output_filepath) > 0:
            print(f"⏭️ MP4 already exists, skipping encode: {output_filepath}")
        else:
            concat_string = "concat:" + "|".join(vob_files)
            ffmpeg_cmd = [
                "ffmpeg", "-y", "-i", concat_string,
                "-c:v", "libx264", "-preset", "fast", "-crf", "28",
                "-c:a", "aac", output_filepath,
            ]
            print("⏳ Encoding to MP4...")
            result = subprocess.run(
                ffmpeg_cmd,
                cwd=found_path,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            if (
                result.returncode != 0
                or not os.path.exists(output_filepath)
                or os.path.getsize(output_filepath) == 0
            ):
                print(f"❌ Encoding failed (return code {result.returncode}). Skipping insert.")
                failed += 1
                continue

        folder_id = _get_or_create_folder(
            folders_collection, raw_folder_name, topic, participants
        )

        print("💾 Saving folder + video to MongoDB...")
        video_doc = build_imported_video(
            title=topic,
            original_folder=raw_folder_name,
            participants=participants,
            media_path=output_filepath,
            folder_id=folder_id,
        )
        videos_collection.insert_one(video_doc)
        imported += 1
        print(f"🎉 Success! status=pending, folder_id={folder_id}")
        print(f"   azure_stream_url={output_filepath}")

    print(f"\n📊 Done. imported={imported}, skipped={skipped}, failed={failed}")


if __name__ == "__main__":
    process_archive()
