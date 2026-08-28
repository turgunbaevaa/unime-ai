from faster_whisper import WhisperModel
import time

AUDIO_FILE = "test_audio.ogg" 

print("Loading the Whisper Large-v3 model...")
start_load = time.time()

model = WhisperModel("large-v3", device="cuda", compute_type="float16")

print(f"Model downloaded in {time.time() - start_load:.2f} seconds.")
print(f"Starting file transcription {AUDIO_FILE}...\n")

start_transcribe = time.time()

segments, info = model.transcribe(AUDIO_FILE, vad_filter=True)

print(f"Language has been determined: {info.language} (probability: {info.language_probability:.2f})\n")

full_text = ""
for segment in segments:
    start_time = f"{segment.start:.2f}s"
    end_time = f"{segment.end:.2f}s"
    
    line = f"[{start_time} -> {end_time}] {segment.text}"
    print(line)
    full_text += segment.text + " "

print(f"\nTranscription completed in {time.time() - start_transcribe:.2f} seconds.")