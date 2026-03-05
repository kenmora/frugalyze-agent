import json
import os
import re
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from openai import AsyncOpenAI
from pydantic import BaseModel

load_dotenv()

BASE_DIR = Path(__file__).resolve().parent.parent
STATIC_DIR = BASE_DIR / "static"
URL_RE = re.compile(r"https?://[^\s<>'\"()]+", re.IGNORECASE)

app = FastAPI(title="Frugalyze Agent App", version="0.1.0")
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")
openai_client: AsyncOpenAI | None = None

try:
    from agents import Agent, Runner

    AGENTS_SDK_AVAILABLE = True
except Exception:
    Agent = None
    Runner = None
    AGENTS_SDK_AVAILABLE = False


class ChatRequest(BaseModel):
    message: str


class ChatResponse(BaseModel):
    reply: str


def extract_first_url(text: str) -> str | None:
    match = URL_RE.search(text)
    if not match:
        return None
    return match.group(0).rstrip(".,!?:;")


def response_text(response: object) -> str:
    text = getattr(response, "output_text", None)
    if isinstance(text, str) and text.strip():
        return text.strip()
    return ""


def get_openai_client() -> AsyncOpenAI:
    global openai_client
    if openai_client is None:
        openai_client = AsyncOpenAI()
    return openai_client


async def classify_input(message: str) -> tuple[str, str | None]:
    default_url = extract_first_url(message)
    if not os.getenv("OPENAI_API_KEY"):
        return ("URL_PROVIDED", default_url) if default_url else ("NO_URL", None)

    classify_response = await get_openai_client().responses.create(
        model="gpt-5-nano",
        input=[
            {
                "role": "system",
                "content": (
                    "Classify whether user input includes a publicly accessible URL. "
                    "Return strict JSON with keys: label, url. "
                    "label must be URL_PROVIDED or NO_URL. "
                    "url must be a URL string only when label is URL_PROVIDED."
                ),
            },
            {"role": "user", "content": message},
        ],
    )
    raw = response_text(classify_response)
    if not raw:
        return ("URL_PROVIDED", default_url) if default_url else ("NO_URL", None)

    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return ("URL_PROVIDED", default_url) if default_url else ("NO_URL", None)

    label = str(data.get("label", "NO_URL")).strip().upper()
    model_url = str(data.get("url", "")).strip() or None
    if label == "URL_PROVIDED":
        return "URL_PROVIDED", model_url or default_url
    return "NO_URL", None


async def analyze_image_url(url: str) -> str:
    if AGENTS_SDK_AVAILABLE:
        try:
            image_agent = Agent(
                name="Image URL Analyzer",
                instructions=(
                    "The user gives a public image URL. Describe what is in the image "
                    "concisely. If uncertain, say what is uncertain."
                ),
                model="gpt-4.1-mini",
            )
            result = await Runner.run(
                image_agent,
                [
                    {
                        "role": "user",
                        "content": [
                            {"type": "input_text", "text": "What is in this image?"},
                            {"type": "input_image", "image_url": url},
                        ],
                    }
                ],
            )
            agent_output = str(getattr(result, "final_output", "")).strip()
            if agent_output:
                return agent_output
        except Exception:
            pass

    analysis_response = await get_openai_client().responses.create(
        model="gpt-4.1-mini",
        input=[
            {
                "role": "user",
                "content": [
                    {"type": "input_text", "text": "What is in this image?"},
                    {"type": "input_image", "image_url": url},
                ],
            }
        ],
    )
    return response_text(analysis_response)


@app.get("/")
def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/api/chat", response_model=ChatResponse)
async def chat(payload: ChatRequest) -> ChatResponse:
    if not payload.message.strip():
        return ChatResponse(reply="Please enter text or a publicly accessible image URL.")

    if not os.getenv("OPENAI_API_KEY"):
        return ChatResponse(reply="OPENAI_API_KEY is missing in your .env file.")

    try:
        label, url = await classify_input(payload.message)
        if label != "URL_PROVIDED" or not url:
            return ChatResponse(
                reply="No public URL detected. Please paste an image URL to analyze."
            )

        description = await analyze_image_url(url)
        if not description:
            return ChatResponse(
                reply=f"URL detected ({url}) but I could not analyze the image."
            )

        return ChatResponse(reply=f"URL detected: {url}\n\nImage analysis:\n{description}")
    except Exception as exc:
        return ChatResponse(reply=f"Request failed: {exc}")
