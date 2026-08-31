import os

from dotenv import load_dotenv

load_dotenv()


def _str(name, default):
    value = os.environ.get(name)
    if value is None or not value.strip():
        return default
    return value.strip()


def _int(name, default):
    return int(_str(name, str(default)))


# MongoDB
MONGO_URI = _str("MONGO_URI", "mongodb://localhost:27017/")
MONGO_DB_NAME = _str("MONGO_DB_NAME", "unime_video_catalog")
MONGO_COLLECTION = _str("MONGO_COLLECTION", "videos")
FOLDERS_COLLECTION = _str("FOLDERS_COLLECTION", "folders")

# Archive import (import_videos.py)
IMPORT_BASE_DIR = _str(
    "IMPORT_BASE_DIR",
    r"C:\Users\ciam\unime.it\Part-Time Multimedia - Backup_CD",
)
IMPORT_OUTPUT_DIR = _str("IMPORT_OUTPUT_DIR", r"C:\UniMe-AI\converted_videos")
IMPORT_LIST_FILE = _str("IMPORT_LIST_FILE", "")  # empty → {IMPORT_BASE_DIR}/Elenco video.txt
IMPORT_MAX_VIDEOS = _int("IMPORT_MAX_VIDEOS", 0)  # 0 = no limit; set 1 to test one video

# Whisper / faster-whisper
DEVICE = _str("DEVICE", "cuda").lower()
WHISPER_MODEL = _str("WHISPER_MODEL", "large-v3")
WHISPER_COMPUTE_TYPE = _str("WHISPER_COMPUTE_TYPE", "float16")
WHISPER_NUM_WORKERS = _int("WHISPER_NUM_WORKERS", 4)
WHISPER_BATCH_SIZE = _int("WHISPER_BATCH_SIZE", 16)
WHISPER_BEAM_SIZE = _int("WHISPER_BEAM_SIZE", 2)

# Ollama
OLLAMA_URL = _str("OLLAMA_URL", "http://localhost:11434/api/generate")
OLLAMA_MODEL = _str("OLLAMA_MODEL", "gemma4:latest")
OLLAMA_NUM_CTX = _int("OLLAMA_NUM_CTX", 24576)
OLLAMA_NUM_BATCH = _int("OLLAMA_NUM_BATCH", 2048)
OLLAMA_TIMEOUT_SECONDS = _int("OLLAMA_TIMEOUT_SECONDS", 600)

# Worker runtime
TEMP_DIR = _str("TEMP_DIR", ".")
STALE_PROCESSING_MINUTES = _int("STALE_PROCESSING_MINUTES", 30)
DOWNLOAD_QUEUE_SIZE = _int("DOWNLOAD_QUEUE_SIZE", 3)
WHISPER_QUEUE_SIZE = _int("WHISPER_QUEUE_SIZE", 2)
MAX_SUMMARY_RETRIES = _int("MAX_SUMMARY_RETRIES", 3)
OMP_NUM_THREADS = _str("OMP_NUM_THREADS", "8")

os.environ["OMP_NUM_THREADS"] = OMP_NUM_THREADS
os.makedirs(TEMP_DIR, exist_ok=True)
