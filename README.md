# UniMe Video Catalog 🎓🎬

An enterprise-grade, AI-powered video catalog and management system developed as an internship project at the University of Messina. 

This platform automatically processes video content, generates highly accurate transcripts using local speech-to-text models, and creates intelligent summaries using Large Language Models (LLMs).

## 🚀 Key Features

* **Automated AI Pipeline:** Seamlessly extracts audio, transcribes speech, and summarizes content without human intervention.
* **Local & Privacy-First AI:** Utilizes locally hosted `faster-whisper` and `Ollama` models, ensuring data privacy and no API costs.
* **Asynchronous Processing:** The Python worker safely polls the database, manages load using a queuing system, and prevents hardware overheating during intensive GPU/CPU tasks.
* **Modern Web Interface:** Built with Next.js and Tailwind CSS, featuring beautiful Markdown rendering (`react-markdown` + `@tailwindcss/typography`) for AI-generated summaries.
* **Robust Status Tracking:** MongoDB meticulously tracks video states (`pending`, `processing`, `completed`, `failed`).

## 🏗️ System Architecture

The project follows a decoupled architecture, divided into two main components that communicate via MongoDB:

1. **Frontend (Next.js):** 
   Provides the User Interface. It fetches video metadata, transcripts, and AI summaries directly from the database and displays them using a highly optimized, responsive web application.
2. **AI Worker (Python):** 
   An asynchronous background service that polls MongoDB for `pending` videos. It downloads/extracts audio via `FFmpeg`, transcribes it using `faster-whisper`, and generates comprehensive summaries using `Ollama`, updating the database in real-time.

## 🛠️ Tech Stack

**Frontend:**
* [Next.js](https://nextjs.org/) (React Framework)
* [Tailwind CSS](https://tailwindcss.com/) & Tailwind Typography
* React Markdown & Remark GFM

**AI & Backend Worker:**
* [Python 3](https://www.python.org/) & `asyncio`
* [faster-whisper](https://github.com/SYSTRAN/faster-whisper) (Optimized speech recognition)
* [Ollama](https://ollama.ai/) (Local LLM runner)
* FFmpeg (Audio/Video processing)

**Database:**
* [MongoDB](https://www.mongodb.com/) (NoSQL document storage)

## 🚦 Getting Started

### Prerequisites
* Node.js (v18+)
* Python (3.9+)
* FFmpeg installed on the system
* MongoDB instance running
* Ollama installed and running locally

### 1. Setup the AI Worker

```bash
cd ai_worker
python -m venv .venv
source .venv/Scripts/activate  # On Windows: .\.venv\Scripts\activate
pip install -r requirements.txt
```

*Note: Ensure your MongoDB URI is set up securely (e.g., via `.env` file).*

Run the worker:

```bash
python ai_worker.py
```

### 2. Setup the Frontend

```bash
cd frontend
npm install
npm run dev
```

Open `http://localhost:3000` to view the application.

## 🧠 Hardware Optimization
The AI worker is intentionally designed with MVP and local hardware constraints in mind:
* **Thread limits:** FFmpeg is restricted to 2 CPU threads to prevent system lockups.
* **Cooling phases:** Built-in sleep intervals between heavy GPU processing tasks prevent thermal throttling on consumer-grade hardware.
* **Batch processing:** Polling is capped to process a set limit of videos per run, ensuring system stability.

## 👨‍💻 Author
**Aruuke Turgunbaeva**  
*Data Analysis & Computer Science*  
*University of Messina*