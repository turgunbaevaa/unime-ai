from db import get_videos_collection
from video_schema import STATUS_PENDING

videos_collection = get_videos_collection()

# Reset AI processing fields for completed videos
result = videos_collection.update_many(
    {"ai_processing.status": "completed"},
    {"$set": {
        "ai_processing.status": STATUS_PENDING,
        "ai_processing.transcript_segments": [],
        "ai_processing.transcription_status": "pending",
        "ai_processing.summary_status": "pending"
    }}
)

print(f"Successfully reset {result.modified_count} videos back to the pending queue!")
