import requests
import time

# The exact text we just extracted using Whisper
transcript_text = """
Humans had no idea what purpose the heart served. In fact, the organ so confused Leonardo da Vinci that he gave up studying it. Although everyone could feel their own heart beating, it wasn't always clear what each thump was achieving. Now we know that the heart pumps blood. But that fact wasn't always obvious, because if a heart was exposed or taken out, the body would perish quickly. It's also impossible to see through the blood vessels. And even if that were possible, the blood itself is opaque, making it difficult to see the heart valves working. Even in the 21st century, only a few people in surgery teams have actually seen a working heart. Internet searches for heart function point to crude models, diagrams, or animations that don't really show how it works. It's as if there has been a centuries-old conspiracy amongst teachers and students to accept that heart function cannot be demonstrated, meaning that the next person would be a doctor. The best thing is simply to cut it open and label the parts.
"""

# Create the prompt for the model
prompt = f"Please write a short and concise summary of the following text:\n\n{transcript_text}"

# Local Ollama API settings
url = "http://localhost:11434/api/generate"
payload = {
    "model": "llama3.1",
    "prompt": prompt,
    "stream": False # Receive the full response at once instead of streaming word by word
}

print("Sending transcription text to the local LLM (Ollama)...")
start_time = time.time()

# Make an HTTP POST request (as the professor requested: "via server api")
response = requests.post(url, json=payload)

if response.status_code == 200:
    result = response.json()
    print(f"Summary generated in {time.time() - start_time:.2f} seconds!\n")
    print("=== SUMMARY ===")
    print(result["response"].strip())
    print("===============")
else:
    print(f"API Error: {response.status_code} - {response.text}")