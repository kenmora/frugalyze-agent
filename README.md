# Frugalyze Agent Foundation

Small local Python web app foundation for a chat interface, ready for OpenAI ChatKit + Agents SDK wiring.

## Framework choice

This setup uses **FastAPI**:
- Lightweight and quick to iterate locally
- Native async support for chat/streaming routes
- Runs on a local port via `uvicorn`
- Easy to expand into WebSocket/SSE and agent tool endpoints

## Prerequisites

- Python 3.11+ installed and available in terminal (`python` or `py`)

## Setup

```powershell
# from project root
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

If your machine uses the Windows launcher:

```powershell
py -m venv .venv
.venv\Scripts\Activate.ps1
py -m pip install -r requirements.txt
```

## Environment

You already have `.env`. Example keys are in `.env.example`.

## Run locally

```powershell
.\scripts\run-dev.ps1
```

This avoids Windows/OneDrive reload loops by watching only `app/` and `static/`.

Then open:
- http://127.0.0.1:8000
- http://127.0.0.1:8000/health

## What is included

- `app/main.py`: FastAPI app with:
  - `/` simple local chat page
  - `/health` health endpoint
  - `/api/chat` workflow:
    - classify input (`gpt-5-nano`) into `URL_PROVIDED` or `NO_URL`
    - when URL is present, analyze image content from the URL using:
      - `prompts/image_classifier/system.txt`
      - `prompts/image_classifier/schema.json`
      - `prompts/image_classifier/config.yaml`
    - returns JSON output (pretty-rendered in UI)
- `static/index.html`: lightweight chat UI for local testing
- `requirements.txt`: core dependencies, including OpenAI Agents SDK and ChatKit

## Next step

Add conversation memory/state and stream partial tokens to the browser.

## Push workflow

Use this helper to commit and push everything changed since the last commit:

```powershell
.\scripts\push-it.ps1
```

Optional custom message:

```powershell
.\scripts\push-it.ps1 -Message "feat: add URL classify + image analyzer flow"
```

What it does:
- stages all tracked/untracked changes
- generates a commit message if none is provided
- commits
- pushes to upstream (or sets upstream on first push when a remote exists)
