import asyncio
import os
import re
import uuid
import time
from datetime import timedelta

try:
    import torch
    torch_lib_path = os.path.join(os.path.dirname(torch.__file__), "lib")
    os.environ["PATH"] = torch_lib_path + os.pathsep + os.environ.get("PATH", "")
except ImportError:
    pass

import config
import requests
import yt_dlp
from faster_whisper import WhisperModel, BatchedInferencePipeline

from db import get_videos_collection
from video_schema import (
    IN_PROGRESS_STATUSES,
    STATUS_COMPLETED,
    STATUS_DOWNLOADING,
    STATUS_EXTRACTING_AUDIO,
    STATUS_FAILED,
    STATUS_PENDING,
    STATUS_SUMMARIZING,
    STATUS_TRANSCRIBING,
    saved_transcript,
    utcnow,
)

FFMPEG_STDERR_TAIL = 2000


def _job_id(video):
    if isinstance(video, dict):
        return str(video.get("_id", "?"))
    return str(video)


def _log(job_id, message):
    print(f"[job {job_id}] {message}")


def _log_error(job_id, exc):
    reason = str(exc).strip().split("\n")[0] or "unknown"
    if len(reason) > 300:
        reason = reason[:297] + "..."
    print(f"[job {job_id}] ERROR {type(exc).__name__}: {reason}")

# --- 1. MongoDB Connection Setup ---
videos_collection = get_videos_collection()

# --- 2. Global Model Initialization ---
print("🚀 Starting High-Throughput Async AI Worker...")
print(f"Loading Whisper {config.WHISPER_MODEL} on {config.DEVICE} ({config.WHISPER_COMPUTE_TYPE})...")
whisper_base = WhisperModel(
    config.WHISPER_MODEL,
    device=config.DEVICE,
    compute_type=config.WHISPER_COMPUTE_TYPE,
    num_workers=config.WHISPER_NUM_WORKERS,
)
whisper_pipeline = BatchedInferencePipeline(model=whisper_base)
print("✅ Whisper model is ready.\n")

# --- 3. Async Queues & GPU Lock ---
gpu_lock = asyncio.Lock()
download_queue = asyncio.Queue(maxsize=config.DOWNLOAD_QUEUE_SIZE)
whisper_queue = asyncio.Queue(maxsize=config.WHISPER_QUEUE_SIZE)
summary_queue = asyncio.Queue()

def reclaim_stale_jobs():
    """Reset processing jobs whose lease has expired (or was never set)."""
    cutoff = utcnow() - timedelta(minutes=config.STALE_PROCESSING_MINUTES)
    result = videos_collection.update_many(
        {
            "ai_processing.status": {"$in": list(IN_PROGRESS_STATUSES)},
            "$or": [
                {"ai_processing.locked_at": {"$lt": cutoff}},
                {"ai_processing.locked_at": {"$exists": False}},
                {"ai_processing.locked_at": None},
            ],
        },
        {
            "$set": {"ai_processing.status": STATUS_PENDING},
            "$unset": {"ai_processing.locked_at": ""},
        },
    )
    recovered = result.modified_count
    print(
        f"♻️ [Recovery] Reclaimed {recovered} job(s) stuck in processing "
        f"(timeout={config.STALE_PROCESSING_MINUTES} min)."
    )
    return recovered

def _set_status(video_id, status, log=True):
    videos_collection.update_one(
        {"_id": video_id},
        {"$set": {"ai_processing.status": status}},
    )
    if log:
        _log(video_id, f"Mongo update status={status}")


def _mark_failed(video_id, error):
    videos_collection.update_one(
        {"_id": video_id},
        {"$set": {"ai_processing.status": STATUS_FAILED, "ai_processing.error": str(error)}},
    )
    _log(video_id, "Mongo update status=failed")


def _mark_summary_failed(video_id, error):
    videos_collection.update_one(
        {"_id": video_id},
        {
            "$set": {"ai_processing.status": STATUS_FAILED, "ai_processing.error": str(error)},
            "$inc": {"ai_processing.retry_count": 1},
        },
    )
    _log(video_id, "Mongo update status=failed (summary retry incremented)")


def _media_url_error(video_url):
    if video_url is None:
        return "azure_stream_url is missing"
    if not isinstance(video_url, str):
        return "azure_stream_url must be a string"
    if not video_url.strip():
        return "azure_stream_url is empty"
    return None


def _decode_stderr(stderr_bytes):
    if not stderr_bytes:
        return ""
    text = stderr_bytes.decode("utf-8", errors="replace").strip()
    if len(text) > FFMPEG_STDERR_TAIL:
        return text[-FFMPEG_STDERR_TAIL:]
    return text

def _audio_file_is_valid(path):
    return os.path.exists(path) and os.path.getsize(path) > 0

def _cleanup_temp_file(path, reason, job_id=None):
    if path and os.path.exists(path):
        os.remove(path)
        if job_id is not None:
            _log(job_id, f"Cleanup {reason}")
        else:
            print(f"[Cleanup] Removed temp file '{path}' ({reason}).")

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
        batch_size=config.WHISPER_BATCH_SIZE,
        beam_size=config.WHISPER_BEAM_SIZE,
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

# Same prompt as before; {body} is either a transcript chunk or ordered partial summaries.
_SUMMARY_PROMPT = """You are an expert academic assistant. Analyze the following transcript of a university event or lecture and write a comprehensive summary.

IMPORTANT RULE: You MUST write the summary in the EXACT SAME LANGUAGE as the transcript provided below.

Please ensure your summary includes:
1. The context and purpose of the event (who organized it, what is the occasion).
2. The main institutional, scientific, or academic topics discussed.
3. Any cultural, artistic, or informal segments mentioned (if applicable).

Transcript:
{body}"""

_CHARS_PER_TOKEN = 4
_OUTPUT_RESERVE_TOKENS = 2048
_MIN_CHUNK_TOKENS = 512
_SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+")


def _estimate_tokens(text):
    return (len(text) + _CHARS_PER_TOKEN - 1) // _CHARS_PER_TOKEN


def _max_chunk_chars():
    """Largest body we can send while leaving room for the prompt and the reply."""
    overhead = _estimate_tokens(_SUMMARY_PROMPT.format(body=""))
    budget_tokens = config.OLLAMA_NUM_CTX - overhead - _OUTPUT_RESERVE_TOKENS
    budget_tokens = max(_MIN_CHUNK_TOKENS, budget_tokens)
    return budget_tokens * _CHARS_PER_TOKEN


def _hard_wrap(text, max_chars):
    """Split on whitespace with no overlap. Last resort for a single oversize token."""
    words = text.split()
    if not words:
        return []
    chunks = []
    current = []
    current_len = 0
    for word in words:
        if len(word) > max_chars:
            if current:
                chunks.append(" ".join(current))
                current = []
                current_len = 0
            for i in range(0, len(word), max_chars):
                chunks.append(word[i:i + max_chars])
            continue
        extra = 1 if current else 0
        if current and current_len + extra + len(word) > max_chars:
            chunks.append(" ".join(current))
            current = [word]
            current_len = len(word)
        else:
            current.append(word)
            current_len += extra + len(word)
    if current:
        chunks.append(" ".join(current))
    return chunks


def _pack_units(units, max_chars, joiner):
    """Greedy left-to-right packing. Units are never repeated (no overlap)."""
    chunks = []
    buf = []
    buf_len = 0
    join_len = len(joiner)
    for unit in units:
        unit = unit.strip()
        if not unit:
            continue
        extra = join_len if buf else 0
        if buf and buf_len + extra + len(unit) > max_chars:
            chunks.append(joiner.join(buf))
            buf = []
            buf_len = 0
            extra = 0
        if len(unit) > max_chars:
            if buf:
                chunks.append(joiner.join(buf))
                buf = []
                buf_len = 0
            chunks.extend(_split_oversized_unit(unit, max_chars))
            continue
        buf.append(unit)
        buf_len += extra + len(unit)
    if buf:
        chunks.append(joiner.join(buf))
    return chunks


def _split_oversized_unit(unit, max_chars):
    sentences = [part.strip() for part in _SENTENCE_SPLIT.split(unit) if part.strip()]
    if len(sentences) > 1:
        packed = _pack_units(sentences, max_chars, " ")
        out = []
        for part in packed:
            if len(part) <= max_chars:
                out.append(part)
            else:
                out.extend(_hard_wrap(part, max_chars))
        return out
    return _hard_wrap(unit, max_chars)


def _split_into_chunks(text, max_chars):
    text = text.strip()
    if not text:
        return []
    if len(text) <= max_chars:
        return [text]
    paragraphs = [part.strip() for part in re.split(r"\n\s*\n", text) if part.strip()]
    if len(paragraphs) <= 1:
        return _split_oversized_unit(text, max_chars)
    return _pack_units(paragraphs, max_chars, "\n\n")


def _join_partial_summaries(partials):
    total = len(partials)
    return "\n\n".join(
        f"Part {index} of {total}:\n{part.strip()}"
        for index, part in enumerate(partials, 1)
        if part and part.strip()
    )


def _ollama_complete(prompt):
    payload = {
        "model": config.OLLAMA_MODEL,
        "prompt": prompt,
        "stream": False,
        "options": {
            "num_ctx": config.OLLAMA_NUM_CTX,
            "num_batch": config.OLLAMA_NUM_BATCH
        }
    }
    response = requests.post(
        config.OLLAMA_URL,
        json=payload,
        timeout=config.OLLAMA_TIMEOUT_SECONDS,
    )
    response.raise_for_status()
    return response.json().get("response", "").strip()


def _summarize_body(body, job_id=None):
    max_chars = _max_chunk_chars()
    chunks = _split_into_chunks(body, max_chars)
    if not chunks:
        return "No text available to summarize."
    if len(chunks) == 1:
        return _ollama_complete(_SUMMARY_PROMPT.format(body=chunks[0]))

    if job_id is not None:
        _log(
            job_id,
            f"Ollama splitting transcript ({len(body)} chars, max {max_chars}) into {len(chunks)} chunks",
        )
    partials = []
    for index, chunk in enumerate(chunks, 1):
        if job_id is not None:
            _log(job_id, f"Ollama chunk {index}/{len(chunks)}")
        partials.append(_ollama_complete(_SUMMARY_PROMPT.format(body=chunk)))
    return _summarize_body(_join_partial_summaries(partials), job_id=job_id)


def _run_ollama_sync(text_transcript, job_id=None):
    if not text_transcript or not text_transcript.strip():
        return "No text available to summarize."
    return _summarize_body(text_transcript.strip(), job_id=job_id)

# --- Async Workers ---

async def db_poller():
    """It constantly searches for new videos and adds them to the download/extraction queue."""
    while True:
        if download_queue.qsize() < config.DOWNLOAD_QUEUE_SIZE:
            video = await asyncio.to_thread(
                videos_collection.find_one_and_update,
                {
                    "$or": [
                        {"ai_processing.status": STATUS_PENDING},
                        {
                            "$and": [
                                {"ai_processing.status": STATUS_FAILED},
                                {"ai_processing.whisper_transcript": {"$gt": ""}},
                                {
                                    "$or": [
                                        {"ai_processing.retry_count": {"$lt": config.MAX_SUMMARY_RETRIES}},
                                        {"ai_processing.retry_count": {"$exists": False}},
                                        {"ai_processing.retry_count": None},
                                    ],
                                },
                            ],
                        },
                    ]
                },
                {"$set": {
                    "ai_processing.status": STATUS_DOWNLOADING,
                    "ai_processing.locked_at": utcnow(),
                }}
            )
            
            if video:
                job_id = _job_id(video)
                _log(job_id, "Claimed")
                _log(job_id, "Mongo update status=downloading")
                await download_queue.put(video)
            else:
                await asyncio.sleep(5) 
        else:
            await asyncio.sleep(2) 
            

async def download_worker(worker_id):
    """Consumer 1: Extracts audio on CPU asynchronously."""
    while True:
        video = await download_queue.get()
        job_id = _job_id(video)
        video_url = video.get("azure_stream_url")
        temp_filename = None
        handed_off = False

        try:
            existing_transcript = saved_transcript(video)
            if existing_transcript:
                _log(job_id, "Resume: transcript present, skip download/extract/whisper")
                await asyncio.to_thread(_set_status, video["_id"], STATUS_SUMMARIZING)
                await summary_queue.put((video, existing_transcript))
                handed_off = True
            else:
                media_error = _media_url_error(video_url)
                if media_error:
                    raise RuntimeError(media_error)
                temp_filename = os.path.join(config.TEMP_DIR, f"temp_audio_{uuid.uuid4().hex}.wav")
                await asyncio.to_thread(_set_status, video["_id"], STATUS_DOWNLOADING, False)
                if os.path.exists(video_url) or video_url[1:3] == ":\\":
                    _log(job_id, "Download started (local file)")
                    _log(job_id, "Download completed")
                    await asyncio.to_thread(_set_status, video["_id"], STATUS_EXTRACTING_AUDIO)
                    _log(job_id, "Audio extraction started")
                    process = await asyncio.create_subprocess_exec(
                        'ffmpeg', '-y', '-threads', '2', '-i', video_url, '-vn', '-acodec', 'pcm_s16le', '-ar', '16000', '-ac', '1', temp_filename,
                        stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.PIPE
                    )
                    _, stderr_bytes = await process.communicate()
                    stderr_tail = _decode_stderr(stderr_bytes)
                    if process.returncode != 0:
                        raise RuntimeError(
                            f"FFmpeg failed with return code {process.returncode}: {stderr_tail}"
                        )
                    if not _audio_file_is_valid(temp_filename):
                        raise RuntimeError(
                            f"FFmpeg produced empty or missing audio output. stderr: {stderr_tail}"
                        )
                    _log(job_id, "Audio extraction completed")
                    _log(job_id, "Audio normalization completed (16kHz mono pcm)")
                else:
                    _log(job_id, "Download started")
                    _log(job_id, "Audio extraction started")
                    await asyncio.to_thread(_set_status, video["_id"], STATUS_EXTRACTING_AUDIO)
                    await asyncio.to_thread(_download_yt_dlp, video_url, temp_filename)
                    if not _audio_file_is_valid(temp_filename):
                        raise RuntimeError("Audio extraction produced empty or missing output file")
                    _log(job_id, "Download completed")
                    _log(job_id, "Audio extraction completed")
                    _log(job_id, "Audio normalization skipped (yt-dlp wav passthrough)")

                await whisper_queue.put((video, temp_filename))
                handed_off = True
        except Exception as e:
            _log_error(job_id, e)
            await asyncio.to_thread(_mark_failed, video["_id"], e)
        finally:
            if not handed_off:
                _cleanup_temp_file(temp_filename, "extraction failed", job_id)
            download_queue.task_done()

async def whisper_worker():
    """Consumer 2: Transcribes audio on GPU using batching."""
    while True:
        video, audio_path = await whisper_queue.get()
        job_id = _job_id(video)
        try:
            _log(job_id, "Whisper start")
            await asyncio.to_thread(_set_status, video["_id"], STATUS_TRANSCRIBING)
            async with gpu_lock:
                full_text, segments_data, language = await asyncio.to_thread(_run_whisper_sync, audio_path)
            _log(job_id, "Whisper end")

            await asyncio.to_thread(
                videos_collection.update_one,
                {"_id": video["_id"]},
                {"$set": {
                    "ai_processing.whisper_transcript": full_text,
                    "ai_processing.transcript_segments": segments_data,
                    "ai_processing.language": language,
                    "ai_processing.transcription_status": "completed",
                }}
            )
            _log(job_id, "Mongo update transcript")
            await summary_queue.put((video, full_text))
        except Exception as e:
            _log_error(job_id, e)
            await asyncio.to_thread(_mark_failed, video["_id"], e)
        finally:
            _cleanup_temp_file(audio_path, "whisper finished", job_id)
            whisper_queue.task_done()

async def summary_worker():
    """Consumer 3: Generates summary via Ollama."""
    while True:
        video, full_text = await summary_queue.get()
        job_id = _job_id(video)
        try:
            _log(job_id, "Ollama start")
            await asyncio.to_thread(_set_status, video["_id"], STATUS_SUMMARIZING)
            async with gpu_lock:
                summary_text = await asyncio.to_thread(_run_ollama_sync, full_text, job_id)
            _log(job_id, "Ollama end")

            await asyncio.to_thread(
                videos_collection.update_one,
                {"_id": video["_id"]},
                {"$set": {
                    "ai_processing.status": STATUS_COMPLETED,
                    "ai_processing.llm_summary": summary_text,
                    "ai_processing.transcription_status": "completed",
                    "ai_processing.summary_status": "completed"
                }}
            )
            _log(job_id, "Mongo update status=completed")
        except Exception as e:
            _log_error(job_id, e)
            await asyncio.to_thread(_mark_summary_failed, video["_id"], e)
        finally:
            summary_queue.task_done()
            print("[Cooling..] We give the equipment 30 seconds to cool down...")
            await asyncio.sleep(30)

            

async def main():
    await asyncio.to_thread(reclaim_stale_jobs)
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