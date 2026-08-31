# UniMe AI worker

Python AI pipeline for the UniMe Video Catalog (University of Messina).

This repository is **only** the worker. It polls MongoDB, extracts audio, transcribes with local faster-whisper, and summarizes with local Ollama. The catalog UI and API live in a separate repository. The two sides share the `videos` collection.

## Architecture

```
Video Catalog (other repo)  -->  MongoDB `videos`
                                      ^
ai_worker.py  --poll-->  FFmpeg / yt-dlp  -->  faster-whisper  -->  Ollama
                                      |
                               writes ai_processing.*
```

- **`config.py`** loads `.env` (via `python-dotenv`) and defaults.
- **`db.py`** is the shared Mongo client.
- **`video_schema.py`** is the document shape the importer writes and the worker reads.
- There is no HTTP API in this repo. Jobs are Mongo documents.

## Pipeline

Claim (`pending`, or `failed` with a saved transcript) → `downloading` → `extracting_audio` → `transcribing` → `summarizing` → `completed`.

Any stage can go to `failed`. Failures keep `whisper_transcript` if it was already saved. A later claim then skips Whisper and resumes at summarization.

Media source is **`azure_stream_url` only**. A local path is treated as a file (FFmpeg: 16 kHz mono PCM). Anything else is fetched with yt-dlp.

Long transcripts are split into ordered, non-overlapping chunks, summarized per chunk, then merged with the same prompt. Chunk size is derived from `OLLAMA_NUM_CTX` (prompt overhead + 2048-token reply reserve, ~4 chars/token).

On startup, jobs left in `downloading` / `extracting_audio` / `transcribing` / `summarizing` (or legacy `processing`) longer than `STALE_PROCESSING_MINUTES` are reset to `pending`. Transcripts are not deleted.

After each summary attempt the process sleeps 30 seconds (cooling). FFmpeg is limited to 2 CPU threads.

## Configuration

Copy `.env.example` to `.env`. `config.py` reads environment variables and falls back to the defaults below. Empty values use the default.

Do not put secrets in git. `.env` is gitignored.

## Environment variables

| Variable | Default | Role |
|---|---|---|
| `MONGO_URI` | `mongodb://localhost:27017/` | Mongo connection |
| `MONGO_DB_NAME` | `unime_video_catalog` | Database name |
| `MONGO_COLLECTION` | `videos` | Collection name |
| `DEVICE` | `cuda` | `cuda` or `cpu` (no auto-detect) |
| `WHISPER_MODEL` | `large-v3` | faster-whisper model |
| `WHISPER_COMPUTE_TYPE` | `float16` | e.g. `int8` on CPU |
| `WHISPER_NUM_WORKERS` | `4` | Whisper workers |
| `WHISPER_BATCH_SIZE` | `16` | Batched ASR |
| `WHISPER_BEAM_SIZE` | `2` | Beam search |
| `OLLAMA_URL` | `http://localhost:11434/api/generate` | Generate endpoint |
| `OLLAMA_MODEL` | `gemma4:latest` | Must already be pulled in Ollama |
| `OLLAMA_NUM_CTX` | `24576` | Context window |
| `OLLAMA_NUM_BATCH` | `2048` | Ollama batch |
| `OLLAMA_TIMEOUT_SECONDS` | `600` | HTTP timeout |
| `TEMP_DIR` | `.` | Temp WAV directory |
| `STALE_PROCESSING_MINUTES` | `30` | Lease reclaim on startup |
| `DOWNLOAD_QUEUE_SIZE` | `3` | In-flight download cap |
| `OMP_NUM_THREADS` | `8` | OpenMP threads |

## Installation

Prerequisites: Python 3.9+, FFmpeg on `PATH`, MongoDB, Ollama running with `OLLAMA_MODEL` pulled.

```bash
python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env               # then edit
```

The worker also needs a CUDA stack if `DEVICE=cuda` (see GPU requirements).

## GPU requirements

Default settings assume an NVIDIA GPU:

- `DEVICE=cuda`
- `WHISPER_COMPUTE_TYPE=float16`
- Whisper `large-v3` loaded at **import time**
- Ollama uses the same machine; Whisper stays in VRAM while Ollama runs (a lock serializes inference, not memory)

For CPU:

```
DEVICE=cpu
WHISPER_COMPUTE_TYPE=int8
```

`float16` on CPU often fails. First start still downloads `large-v3` if it is not cached.

## How to run

1. MongoDB reachable at `MONGO_URI`.
2. Ollama serving `OLLAMA_MODEL` at `OLLAMA_URL`.
3. Catalog or importer has inserted videos with `ai_processing.status=pending` and a usable `azure_stream_url`.

```bash
python ai_worker.py
```

Logs are print-based: `[job <Mongo _id>]` plus `ERROR <Type>: <reason>`.

**Archive ingest** (DVD VOB → MP4, then Mongo). Paths inside `import_videos.py` are machine-specific (`C:\Users\ciam\...`). Encoding must succeed or the row is not inserted. New rows are `pending` with `azure_stream_url` set to the converted file.

```bash
python import_videos.py
```

**Re-queue completed jobs** (does not clear full transcripts, only segments + nested status flags):

```bash
python fix_db.py
```

## How to test

There is no pytest suite. Two manual scripts exist; they do **not** use `config.py`.

```bash
# Needs test_audio.ogg, CUDA, and faster-whisper large-v3
python test_whisper.py

# Needs Ollama; hardcoded model llama3.1 (not gemma4:latest)
python test_summary.py
```

## Troubleshooting

| Symptom | Likely cause |
|---|---|
| Process dies at startup | `DEVICE=cuda` but no GPU, or `float16` on CPU. Set `DEVICE` / `WHISPER_COMPUTE_TYPE`. |
| `ffmpeg: command not found` | Install FFmpeg and ensure it is on `PATH`. |
| Ollama errors / timeouts | Ollama not running, wrong `OLLAMA_URL`, or model not pulled (`ollama pull gemma4:latest`). |
| Jobs never claimed | Status is not `pending` (old imports used `awaiting_upload`). `azure_stream_url` empty. |
| Jobs stuck mid-pipeline | Restart the worker; stale in-progress jobs older than `STALE_PROCESSING_MINUTES` return to `pending`. |
| Whisper re-runs after a summary failure | Transcript was never saved. If `whisper_transcript` is non-empty, the worker skips Whisper. |
| yt-dlp / missing wav | Remote URL path; output name may not match the queued `.wav`. Prefer a local file in `azure_stream_url`. |

## Repository structure

```
ai_worker.py       # asyncio worker (poll → extract → ASR → LLM)
config.py          # env + defaults
db.py              # Mongo collection helper
video_schema.py    # import document + status constants
import_videos.py   # one-shot DVD archive ingest
fix_db.py          # reset completed → pending
test_whisper.py    # manual ASR smoke test
test_summary.py    # manual Ollama smoke test
test_audio.ogg     # fixture for test_whisper.py
requirements.txt
.env.example
```

## Author

Aruuke Turgunbaeva — Data Analysis & Computer Science, University of Messina.
