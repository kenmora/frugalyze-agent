import json
import os
import re
from functools import lru_cache
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from openai import AsyncOpenAI
from pydantic import BaseModel
import yaml

load_dotenv()

BASE_DIR = Path(__file__).resolve().parent.parent
STATIC_DIR = BASE_DIR / "static"
URL_RE = re.compile(r"https?://[^\s<>'\"()]+", re.IGNORECASE)

app = FastAPI(title="Frugalyze Agent App", version="0.1.0")
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")
openai_client: AsyncOpenAI | None = None


class ChatRequest(BaseModel):
    message: str


class ChatResponse(BaseModel):
    reply: str
    usage: dict[str, int] | None = None
    json_data: dict[str, Any] | list[Any] | None = None


def extract_first_url(text: str) -> str | None:
    match = URL_RE.search(text)
    if not match:
        return None
    return match.group(0).rstrip(".,!?:;")


def response_text(response: object) -> str:
    text = getattr(response, "output_text", None)
    if isinstance(text, str) and text.strip():
        return text.strip()
    output_items = getattr(response, "output", None)
    if isinstance(output_items, list):
        chunks: list[str] = []
        for item in output_items:
            contents = getattr(item, "content", None)
            if not isinstance(contents, list):
                continue
            for entry in contents:
                entry_text = getattr(entry, "text", None)
                if isinstance(entry_text, str) and entry_text.strip():
                    chunks.append(entry_text.strip())
        if chunks:
            return "\n".join(chunks)
    return ""


def response_usage(response: object) -> dict[str, int]:
    usage = getattr(response, "usage", None)
    if usage is None:
        return {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0}

    input_tokens = int(getattr(usage, "input_tokens", 0) or 0)
    output_tokens = int(getattr(usage, "output_tokens", 0) or 0)
    total_tokens = int(getattr(usage, "total_tokens", input_tokens + output_tokens) or 0)
    return {
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "total_tokens": total_tokens,
    }


def get_openai_client() -> AsyncOpenAI:
    global openai_client
    if openai_client is None:
        openai_client = AsyncOpenAI()
    return openai_client


def resolve_project_path(path_value: str) -> Path:
    raw_path = Path(path_value)
    if raw_path.is_absolute():
        return raw_path
    return BASE_DIR / raw_path


@lru_cache(maxsize=8)
def load_prompt_bundle(config_rel_path: str) -> dict[str, Any]:
    config_path = resolve_project_path(config_rel_path)
    if not config_path.exists():
        raise FileNotFoundError(f"Prompt config file not found: {config_path}")

    config = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}

    prompt_cfg = config.get("prompt", {})
    model_cfg = config.get("model", {})
    response_cfg = config.get("response", {})

    system_file = resolve_project_path(str(prompt_cfg.get("system_file", "")))
    schema_file = resolve_project_path(str(prompt_cfg.get("schema_file", "")))
    if not system_file.exists():
        raise FileNotFoundError(f"System prompt file not found: {system_file}")
    if not schema_file.exists():
        raise FileNotFoundError(f"Schema file not found: {schema_file}")

    system_prompt = system_file.read_text(encoding="utf-8").strip()
    schema_json = json.loads(schema_file.read_text(encoding="utf-8"))
    schema_name = str(response_cfg.get("schema_name", schema_json.get("name", "schema")))

    return {
        "config_path": str(config_path),
        "system_prompt": system_prompt,
        "schema_name": schema_name,
        "schema": schema_json.get("schema", schema_json),
        "strict_schema": bool(response_cfg.get("strict_schema", True)),
        "response_format": str(response_cfg.get("format", "json_schema")),
        "model_id": str(model_cfg.get("id", "gpt-5-nano")),
        "reasoning_effort": str(model_cfg.get("reasoning_effort", "medium")),
        "verbosity": str(model_cfg.get("verbosity", "low")),
        "max_output_tokens": int(model_cfg.get("max_output_tokens", 1200)),
    }


def parse_json_maybe(text: str) -> dict[str, Any] | list[Any] | None:
    cleaned = text.strip()
    if not cleaned:
        return None
    try:
        value = json.loads(cleaned)
    except json.JSONDecodeError:
        return None
    if isinstance(value, (dict, list)):
        return value
    return None


async def classify_input(message: str) -> tuple[str, str | None, dict[str, int]]:
    default_url = extract_first_url(message)
    if not os.getenv("OPENAI_API_KEY"):
        if default_url:
            return ("URL_PROVIDED", default_url, {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0})
        return ("NO_URL", None, {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0})

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
    classify_usage = response_usage(classify_response)
    raw = response_text(classify_response)
    if not raw:
        if default_url:
            return ("URL_PROVIDED", default_url, classify_usage)
        return ("NO_URL", None, classify_usage)

    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        if default_url:
            return ("URL_PROVIDED", default_url, classify_usage)
        return ("NO_URL", None, classify_usage)

    label = str(data.get("label", "NO_URL")).strip().upper()
    model_url = str(data.get("url", "")).strip() or None
    if label == "URL_PROVIDED":
        return "URL_PROVIDED", model_url or default_url, classify_usage
    return "NO_URL", None, classify_usage


async def analyze_image_url(
    url: str,
) -> tuple[str, dict[str, int], dict[str, Any] | list[Any] | None]:
    bundle = load_prompt_bundle("prompts/image_classifier/config.yaml")

    text_payload: dict[str, Any] = {"verbosity": bundle["verbosity"]}
    if bundle["response_format"] == "json_schema":
        text_payload["format"] = {
            "type": "json_schema",
            "name": bundle["schema_name"],
            "schema": bundle["schema"],
            "strict": bundle["strict_schema"],
        }
    elif bundle["response_format"] == "json_object":
        text_payload["format"] = {"type": "json_object"}

    analysis_response = await get_openai_client().responses.create(
        model=bundle["model_id"],
        reasoning={"effort": bundle["reasoning_effort"]},
        max_output_tokens=bundle["max_output_tokens"],
        text=text_payload,
        input=[
            {"role": "system", "content": bundle["system_prompt"]},
            {
                "role": "user",
                "content": [
                    {"type": "input_text", "text": f"Image URL: {url}"},
                    {"type": "input_image", "image_url": url},
                ],
            },
        ],
    )
    raw = response_text(analysis_response)
    parsed = parse_json_maybe(raw)
    if parsed is not None:
        pretty = json.dumps(parsed, indent=2, ensure_ascii=False)
        return pretty, response_usage(analysis_response), parsed
    return raw, response_usage(analysis_response), None


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
        label, url, classify_usage = await classify_input(payload.message)
        if label != "URL_PROVIDED" or not url:
            return ChatResponse(
                reply="No public URL detected. Please paste an image URL to analyze.",
                usage=classify_usage,
            )

        description, image_usage, json_data = await analyze_image_url(url)
        if not description:
            return ChatResponse(
                reply=f"URL detected ({url}) but I could not analyze the image.",
                usage={
                    "input_tokens": classify_usage["input_tokens"] + image_usage["input_tokens"],
                    "output_tokens": classify_usage["output_tokens"] + image_usage["output_tokens"],
                    "total_tokens": classify_usage["total_tokens"] + image_usage["total_tokens"],
                },
            )

        return ChatResponse(
            reply=f"URL detected: {url}\n\nImage analysis:\n{description}",
            usage={
                "input_tokens": classify_usage["input_tokens"] + image_usage["input_tokens"],
                "output_tokens": classify_usage["output_tokens"] + image_usage["output_tokens"],
                "total_tokens": classify_usage["total_tokens"] + image_usage["total_tokens"],
            },
            json_data=json_data,
        )
    except Exception as exc:
        return ChatResponse(reply=f"Request failed: {exc}")
