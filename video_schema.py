"""Shared video document shape used by the importer and AI worker.

The worker is the source of truth for field names. This module only
initializes documents so they are claimable and writable without gaps.
"""

import re
from datetime import datetime, timezone

STATUS_PENDING = "pending"
STATUS_DOWNLOADING = "downloading"
STATUS_EXTRACTING_AUDIO = "extracting_audio"
STATUS_TRANSCRIBING = "transcribing"
STATUS_SUMMARIZING = "summarizing"
STATUS_COMPLETED = "completed"
STATUS_FAILED = "failed"
# Legacy claim status from before staged transitions.
STATUS_PROCESSING_LEGACY = "processing"

IN_PROGRESS_STATUSES = (
    STATUS_DOWNLOADING,
    STATUS_EXTRACTING_AUDIO,
    STATUS_TRANSCRIBING,
    STATUS_SUMMARIZING,
    STATUS_PROCESSING_LEGACY,
)


def saved_transcript(video):
    """Return stored Whisper text, or empty if transcription has not succeeded."""
    ai = video.get("ai_processing") or {}
    return (ai.get("whisper_transcript") or "").strip()


def utcnow():
    return datetime.now(timezone.utc)


def parse_authors(participants):
    """Split 'Name A, Name B' into an authors array for the catalog API."""
    if not participants or not isinstance(participants, str):
        return []
    return [name.strip() for name in participants.split(",") if name.strip()]


_CD_SUFFIX = re.compile(r"(?:_CD| - CD|-CD)(\d+)$", re.IGNORECASE)
_DATE_IN_NAME = re.compile(r"(\d{1,2})-(\d{1,2})-(\d{4})")


def normalize_folder_key(name):
    """Normalize folder names so txt rows can match on-disk DVD folders.

    Handles date zero-padding, CD suffix variants, spaces vs underscores.
    """
    key = (name or "").strip().lower()
    key = _CD_SUFFIX.sub(lambda m: f"_cd{m.group(1)}", key)

    def _pad_date(match):
        day, month, year = match.groups()
        return f"{int(day):02d}-{int(month):02d}-{year}"

    key = _DATE_IN_NAME.sub(_pad_date, key)
    key = key.replace(" - ", "_")
    key = re.sub(r"\s+", "_", key)
    key = re.sub(r"_+", "_", key).strip("_")
    return key


def parse_conference_part(raw_folder_name):
    """Split a DVD folder name into conference group and disc part.

    Examples:
        14-12-2011_..._Solvay_CD1  -> (14-12-2011_..._Solvay, 1, True)
        23-5-2001 - CD2            -> (23-5-2001, 2, True)
        26-05-2011_Aula_Magna_...  -> (26-05-2011_Aula_Magna_..., 1, False)

    Returns (conference_group, conference_part, is_multi_disc).
    """
    name = (raw_folder_name or "").strip()
    match = _CD_SUFFIX.search(name)
    if match:
        group = name[: match.start()].rstrip(" _-")
        part = int(match.group(1))
        return group, part, True
    return name, 1, False


def display_title(topic, conference_part, is_multi_disc):
    """Catalog title; append part label when the row is one disc of a set."""
    topic = (topic or "").strip()
    if is_multi_disc and conference_part >= 1:
        return f"{topic} (Part {conference_part})"
    return topic


def build_folder(conference_group, title, participants):
    """Folder document for the folders collection (one per conference)."""
    now = utcnow()
    return {
        "name": conference_group,
        "conference_group": conference_group,
        "title": title,
        "participants": participants,
        "authors": parse_authors(participants),
        "created_at": now,
        "updated_at": now,
    }


def initial_ai_processing(status=STATUS_PENDING):
    """Nested ai_processing object expected by the worker."""
    return {
        "status": status,
        "transcription_status": "pending",
        "summary_status": "pending",
        "whisper_transcript": "",
        "transcript_segments": [],
        "llm_summary": "",
        "language": None,
        "error": None,
        "locked_at": None,
        "retry_count": 0,
    }


def build_imported_video(
    title,
    original_folder,
    participants,
    media_path,
    folder_id,
    conference_group,
    conference_part,
):
    """Catalog + worker document after VOB→MP4 import."""
    now = utcnow()
    return {
        "title": title,
        "original_folder": original_folder,
        "participants": participants,
        "authors": parse_authors(participants),
        "folder_id": str(folder_id),
        "conference_group": conference_group,
        "conference_part": conference_part,
        "azure_stream_url": media_path,
        "local_filepath": media_path,
        "tags": [],
        "is_deleted": False,
        "opac_export": {"is_exported": False},
        "created_at": now,
        "updated_at": now,
        "ai_processing": initial_ai_processing(STATUS_PENDING),
    }
