import asyncio
import os
import uuid
import time
import requests
import yt_dlp
from pymongo import MongoClient
from faster_whisper import WhisperModel, BatchedInferencePipeline

# Optimize CPU threads for parallel operations
os.environ["OMP_NUM_THREADS"] = "8"

# --- 1. MongoDB Connection Setup ---
MONGO_URI = "mongodb://localhost:27017/"
DB_NAME = "unime_video_catalog" 
client = MongoClient(MONGO_URI)
db = client[DB_NAME]
videos_collection = db["videos"]

# --- 2. Global Model Initialization ---
print("🚀 Starting High-Throughput Async AI Worker...")
print("Loading Whisper Large-v3 model into VRAM...")
whisper_base = WhisperModel("large-v3", device="cuda", compute_type="float16", num_workers=4)
whisper_pipeline = BatchedInferencePipeline(model=whisper_base)
print("✅ Whisper model is ready.\n")

# --- 3. Async Queues & GPU Lock ---
gpu_lock = asyncio.Lock()
download_queue = asyncio.Queue(maxsize=3)
whisper_queue = asyncio.Queue()
summary_queue = asyncio.Queue()

# --- Synchronous Helpers ---
def _download_yt_dlp(video_url, temp_filename):
    ydl_opts = {
        'format': 'bestaudio/best',
        'outtmpl': temp_filename,
        'postprocessors': [{'key': 'FFmpegExtractAudio', 'preferredcodec': 'wav', 'preferredquality': '192'}],
        'quiet': True, 'noplaylist': True
    }
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        ydl.download([video_url])

def _run_whisper_sync(audio_path):
    segments, info = whisper_pipeline.transcribe(
        audio_path,
        batch_size=16,
        beam_size=2,
        condition_on_previous_text=False,
        vad_parameters=dict(min_silence_duration_ms=2000, speech_pad_ms=400, threshold=0.5)
    )
    
    full_text = ""
    segments_data = []
    for segment in segments:
        full_text += segment.text + " "
        segments_data.append({
            "start_time": round(segment.start, 2),
            "end_time": round(segment.end, 2),
            "text": segment.text.strip()
        })
    return full_text.strip(), segments_data, info.language

def _run_ollama_sync(text_transcript):
    if not text_transcript or not text_transcript.strip():
        return "No text available to summarize."

    url = "http://localhost:11434/api/generate"
    payload = {
        "model": "gemma4:latest", 
        "prompt": f"""You are an expert academic assistant. Analyze the following transcript of a university event or lecture and write a comprehensive summary.

IMPORTANT RULE: You MUST write the summary in the EXACT SAME LANGUAGE as the transcript provided below.

Please ensure your summary includes:
1. The context and purpose of the event (who organized it, what is the occasion).
2. The main institutional, scientific, or academic topics discussed.
3. Any cultural, artistic, or informal segments mentioned (if applicable).

Transcript:
{text_transcript}""",
        "stream": False,
        "options": {
            "num_ctx": 24576,
            "num_batch": 2048
        }
    }
    try:
        response = requests.post(url, json=payload, timeout=600)
        response.raise_for_status() 
        return response.json().get("response", "").strip()
    except Exception as e:
        print(f"❌ Ollama API error: {e}")
        raise e

# --- Async Workers ---

async def db_poller():
    """It constantly searches for new videos and adds them to the download/extraction queue."""
    while True:
        if download_queue.qsize() < 3:
            video = await asyncio.to_thread(
                videos_collection.find_one_and_update,
                {"ai_processing.status": "pending"},
                {"$set": {"ai_processing.status": "processing"}}
            )
            
            if video:
                print(f"🔍 [DB] Found pending video: '{video.get('title')}'")
                await download_queue.put(video)
            else:
                await asyncio.sleep(5) 
        else:
            await asyncio.sleep(2) 
            

async def download_worker(worker_id):
    """Consumer 1: Extracts audio on CPU asynchronously."""
    while True:
        video = await download_queue.get()
        video_url = video.get("azure_stream_url")
        temp_filename = f"temp_audio_{uuid.uuid4().hex}.wav"
        
        print(f"📥 [I/O Worker {worker_id}] Preparing audio for: '{video.get('title')}'")
        try:
            if os.path.exists(video_url) or video_url[1:3] == ":\\":
                process = await asyncio.create_subprocess_exec(
                    'ffmpeg', '-y', '-threads', '2', '-i', video_url, '-vn', '-acodec', 'pcm_s16le', '-ar', '16000', '-ac', '1', temp_filename,
                    stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL
                )
                await process.wait()
            else:
                await asyncio.to_thread(_download_yt_dlp, video_url, temp_filename)
                
            await whisper_queue.put((video, temp_filename))
        except Exception as e:
            print(f"❌ Download error for '{video.get('title')}': {e}")
            await asyncio.to_thread(
                videos_collection.update_one,
                {"_id": video["_id"]},
                {"$set": {"ai_processing.status": "failed", "ai_processing.error": str(e)}}
            )
        finally:
            download_queue.task_done()

async def whisper_worker():
    """Consumer 2: Transcribes audio on GPU using batching."""
    while True:
        video, audio_path = await whisper_queue.get()
        print(f"🧠 [GPU - Whisper] Transcribing: '{video.get('title')}'")
        try:
            async with gpu_lock:
                full_text, segments_data, language = await asyncio.to_thread(_run_whisper_sync, audio_path)
            
            await asyncio.to_thread(
                videos_collection.update_one,
                {"_id": video["_id"]},
                {"$set": {
                    "ai_processing.whisper_transcript": full_text,
                    "ai_processing.transcript_segments": segments_data,
                    "ai_processing.language": language
                }}
            )
            await summary_queue.put((video, full_text))
        except Exception as e:
            print(f"❌ Whisper error for '{video.get('title')}': {e}")
            await asyncio.to_thread(
                videos_collection.update_one,
                {"_id": video["_id"]},
                {"$set": {"ai_processing.status": "failed", "ai_processing.error": str(e)}}
            )
        finally:
            if os.path.exists(audio_path):
                os.remove(audio_path)
            whisper_queue.task_done()

async def summary_worker():
    """Consumer 3: Generates summary via Ollama."""
    while True:
        video, full_text = await summary_queue.get()
        print(f"📝 [GPU - Ollama] Generating summary for: '{video.get('title')}'")
        try:
            async with gpu_lock:
                summary_text = await asyncio.to_thread(_run_ollama_sync, full_text)
            
            await asyncio.to_thread(
                videos_collection.update_one,
                {"_id": video["_id"]},
                {"$set": {
                    "ai_processing.status": "completed",
                    "ai_processing.llm_summary": summary_text,
                    "ai_processing.transcription_status": "completed",
                    "ai_processing.summary_status": "completed"
                }}
            )
            print(f"✅ SUCCESSFULLY COMPLETED: '{video.get('title')}'\n")
        except Exception as e:
            print(f"❌ Ollama error for '{video.get('title')}': {e}")
            await asyncio.to_thread(
                videos_collection.update_one,
                {"_id": video["_id"]},
                {"$set": {"ai_processing.status": "failed", "ai_processing.error": str(e)}}
            )
        finally:
            summary_queue.task_done()
            print("[Cooling..] We give the equipment 30 seconds to cool down...")
            await asyncio.sleep(30)

            

async def main():
    asyncio.create_task(db_poller())
    asyncio.create_task(download_worker(1))
    asyncio.create_task(whisper_worker())
    asyncio.create_task(summary_worker())
    
    while True:
        await asyncio.sleep(3600)

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n🛑 AI Worker gracefully stopped by user.")