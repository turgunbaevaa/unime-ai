import os
import subprocess

import config
from db import get_folders_collection, get_videos_collection
from video_schema import (
    build_folder,
    build_imported_video,
    display_title,
    normalize_folder_key,
    parse_conference_part,
)

_HEADER_HINTS = ("nomi cd", "titolo", "data, titolo", "nome cartella")


def _list_file():
    if config.IMPORT_LIST_FILE:
        return config.IMPORT_LIST_FILE
    return os.path.join(config.IMPORT_BASE_DIR, "Elenco video.txt")


def _is_list_header(raw_folder_name, topic):
    combined = f"{raw_folder_name} {topic}".lower()
    return any(hint in combined for hint in _HEADER_HINTS)


def _build_disk_folder_index(base_dir):
    by_exact = {}
    by_normalized = {}
    for root, dirs, _files in os.walk(base_dir):
        for name in dirs:
            path = os.path.join(root, name)
            by_exact[name] = path
            norm = normalize_folder_key(name)
            by_normalized.setdefault(norm, []).append((name, path))
    return by_exact, by_normalized


def _resolve_dvd_folder(raw_folder_name, by_exact, by_normalized):
    """Return (path, disk_folder_name, was_corrected) or (None, None, False)."""
    if raw_folder_name in by_exact:
        return by_exact[raw_folder_name], raw_folder_name, False

    norm = normalize_folder_key(raw_folder_name)
    matches = by_normalized.get(norm, [])
    if len(matches) == 1:
        disk_name, path = matches[0]
        return path, disk_name, disk_name != raw_folder_name
    if len(matches) > 1:
        names = ", ".join(m[0] for m in matches)
        print(f"❌ Ambiguous folder match for '{raw_folder_name}': {names}")
    return None, None, False


def _get_or_create_folder(folders_collection, conference_group, topic, participants):
    existing = folders_collection.find_one({"name": conference_group})
    if existing:
        return existing["_id"]
    return folders_collection.insert_one(
        build_folder(conference_group, topic, participants)
    ).inserted_id


def _encode_vob_folder(found_path, output_filepath):
    vob_files = [f for f in os.listdir(found_path) if f.endswith(".VOB")]
    vob_files.sort()
    if not vob_files:
        return False, "no VOB files"

    if os.path.isfile(output_filepath) and os.path.getsize(output_filepath) > 0:
        return True, "already exists"

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
        return False, f"ffmpeg return code {result.returncode}"
    return True, "encoded"


def process_archive():
    base_dir = config.IMPORT_BASE_DIR
    txt_file = _list_file()
    output_dir = config.IMPORT_OUTPUT_DIR

    print("🔍 Starting archive batch processing...\n")
    print(f"   Source list: {txt_file}")
    print(f"   DVD root:    {base_dir}")
    print(f"   MP4 output:  {output_dir}")
    print("   Grouping:    CD1/CD2 → one folder, separate videos per disc")
    print("   Matching:    exact name, then normalized (dates/spaces/CD)\n")
    if config.IMPORT_MAX_VIDEOS:
        print(f"   Limit:       {config.IMPORT_MAX_VIDEOS} video(s) (IMPORT_MAX_VIDEOS)\n")

    if not os.path.isfile(txt_file):
        print(f"❌ List file not found: {txt_file}")
        print("   Set IMPORT_LIST_FILE or IMPORT_BASE_DIR in .env")
        return

    videos_collection = get_videos_collection()
    folders_collection = get_folders_collection()
    os.makedirs(output_dir, exist_ok=True)
    by_exact, by_normalized = _build_disk_folder_index(base_dir)

    imported = 0
    skipped = 0
    failed = 0
    corrected = 0

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

        if _is_list_header(raw_folder_name, topic):
            continue

        conference_group, conference_part, is_multi_disc = parse_conference_part(
            raw_folder_name
        )
        catalog_title = display_title(topic, conference_part, is_multi_disc)

        if videos_collection.find_one({"original_folder": raw_folder_name}):
            print(f"⏭️ Skipping (already in DB): {catalog_title}")
            skipped += 1
            continue

        found_path, disk_folder_name, was_corrected = _resolve_dvd_folder(
            raw_folder_name, by_exact, by_normalized
        )
        if not found_path:
            print(f"❌ Folder not found on disk: {raw_folder_name}")
            failed += 1
            continue

        if was_corrected:
            corrected += 1
            print(f"📎 Matched txt '{raw_folder_name}' → disk '{disk_folder_name}'")

        print(f"\n✅ Processing: {catalog_title}")
        if is_multi_disc:
            print(f"   conference: {conference_group} (part {conference_part})")

        output_filename = f"{disk_folder_name}.mp4"
        output_filepath = os.path.join(output_dir, output_filename)

        ok, reason = _encode_vob_folder(found_path, output_filepath)
        if not ok:
            print(f"❌ Encoding failed ({reason}). Skipping insert.")
            failed += 1
            continue
        if reason == "already exists":
            print(f"⏭️ MP4 already exists, skipping encode: {output_filepath}")

        folder_id = _get_or_create_folder(
            folders_collection, conference_group, topic, participants
        )

        print("💾 Saving folder + video to MongoDB...")
        video_doc = build_imported_video(
            title=catalog_title,
            original_folder=raw_folder_name,
            participants=participants,
            media_path=output_filepath,
            folder_id=folder_id,
            conference_group=conference_group,
            conference_part=conference_part,
        )
        videos_collection.insert_one(video_doc)
        imported += 1
        print(f"🎉 Success! status=pending, folder_id={folder_id}")
        print(f"   conference_group={conference_group}, part={conference_part}")
        print(f"   azure_stream_url={output_filepath}")

    print(
        f"\n📊 Done. imported={imported}, skipped={skipped}, "
        f"corrected={corrected}, failed={failed}"
    )


if __name__ == "__main__":
    process_archive()
