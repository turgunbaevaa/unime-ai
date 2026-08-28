from pymongo import MongoClient

# Connect to MongoDB
client = MongoClient("mongodb://localhost:27017/")
db = client["unime_video_catalog"]
videos_collection = db["videos"]

# Reset AI processing fields for completed videos
result = videos_collection.update_many(
    {"ai_processing.status": "completed"},
    {"$set": {
        "ai_processing.status": "pending",
        "ai_processing.transcript_segments": [],
        "ai_processing.transcription_status": "pending",
        "ai_processing.summary_status": "pending"
    }}
)

print(f"Successfully reset {result.modified_count} videos back to the pending queue!")