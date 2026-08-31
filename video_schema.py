"""Shared video document shape used by the importer and AI worker.

The worker is the source of truth for field names. This module only
initializes documents so they are claimable and writable without gaps.
"""

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


def build_folder(original_folder, title, participants):
    """Folder document for the folders collection."""
    now = utcnow()
    return {
        "name": original_folder,
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


def build_imported_video(title, original_folder, participants, media_path, folder_id):
    """Catalog + worker document after VOB→MP4 import."""
    now = utcnow()
    return {
        "title": title,
        "original_folder": original_folder,
        "participants": participants,
        "authors": parse_authors(participants),
        "folder_id": folder_id,
        "azure_stream_url": media_path,
        "local_filepath": media_path,
        "tags": [],
        "is_deleted": False,
        "opac_export": {"is_exported": False},
        "created_at": now,
        "updated_at": now,
        "ai_processing": initial_ai_processing(STATUS_PENDING),
    }
