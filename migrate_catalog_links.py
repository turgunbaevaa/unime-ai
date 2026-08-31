"""Link legacy import videos to folders for the catalog UI.

Groups CD1/CD2 rows under one folder (conference_group). Does not re-encode
or reset AI jobs.
"""

from bson import ObjectId

from db import get_folders_collection, get_videos_collection
from video_schema import (
    build_folder,
    display_title,
    parse_authors,
    parse_conference_part,
    utcnow,
)


def _get_or_create_folder(folders_collection, conference_group, title, participants):
    existing = folders_collection.find_one({"name": conference_group})
    if existing:
        return existing["_id"]
    return folders_collection.insert_one(
        build_folder(conference_group, title, participants)
    ).inserted_id


def _needs_migration(video):
    folder_id = video.get("folder_id")
    if folder_id is None:
        return True
    if isinstance(folder_id, ObjectId):
        return True
    if not video.get("authors"):
        return True
    if not video.get("conference_group"):
        return True
    return False


def migrate_catalog_links(dry_run=False):
    videos = get_videos_collection()
    folders = get_folders_collection()

    query = {
        "original_folder": {"$exists": True, "$ne": ""},
        "$or": [
            {"folder_id": {"$exists": False}},
            {"folder_id": {"$type": "objectId"}},
            {"authors": {"$exists": False}},
            {"authors": []},
            {"conference_group": {"$exists": False}},
        ],
    }

    candidates = list(videos.find(query))
    print(f"Found {len(candidates)} video(s) to migrate.\n")

    updated = 0
    skipped = 0

    for video in candidates:
        vid = video["_id"]
        original_folder = video.get("original_folder", "").strip()
        topic = video.get("title") or original_folder
        participants = video.get("participants") or ""

        if not original_folder:
            print(f"⏭️ Skip {vid}: missing original_folder")
            skipped += 1
            continue

        if not _needs_migration(video):
            skipped += 1
            continue

        conference_group, conference_part, is_multi_disc = parse_conference_part(
            original_folder
        )
        # Strip old "(Part N)" suffix if title was already migrated once.
        base_topic = topic
        if " (Part " in base_topic:
            base_topic = base_topic.rsplit(" (Part ", 1)[0]
        catalog_title = display_title(base_topic, conference_part, is_multi_disc)

        folder_oid = _get_or_create_folder(
            folders, conference_group, base_topic, participants
        )
        folder_id = str(folder_oid)
        authors = video.get("authors") or parse_authors(participants)

        patch = {
            "folder_id": folder_id,
            "authors": authors,
            "conference_group": conference_group,
            "conference_part": conference_part,
            "title": catalog_title,
            "updated_at": utcnow(),
        }
        if "is_deleted" not in video:
            patch["is_deleted"] = False
        if "tags" not in video:
            patch["tags"] = []
        if "opac_export" not in video:
            patch["opac_export"] = {"is_exported": False}

        print(f"{'[dry-run] ' if dry_run else ''}Link {vid}")
        print(f"   title: {catalog_title}")
        print(f"   {original_folder} → group={conference_group}, part={conference_part}")
        print(f"   folder_id={folder_id}")

        if not dry_run:
            videos.update_one({"_id": vid}, {"$set": patch})
        updated += 1

    print(f"\nDone. migrated={updated}, skipped={skipped}")


if __name__ == "__main__":
    import sys

    dry_run = "--dry-run" in sys.argv
    if dry_run:
        print("Dry run — no writes.\n")
    migrate_catalog_links(dry_run=dry_run)
