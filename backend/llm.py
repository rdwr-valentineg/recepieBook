"""LLM providers for extracting structured recipe data from page text or images."""
import asyncio
import base64
import json
import time
import re
from typing import Optional

import httpx

from config import settings
from schemas import ProviderResult, ExtractedRecipe, ProviderInfo


VALID_CATEGORIES = {
    "desserts", "pastries", "bread", "meat", "fish", "salads",
    "pasta", "soups", "stews", "breakfast", "drinks", "other",
}


def build_prompt(page_text: str, page_title: str, url: str) -> str:
    return f"""You are extracting a structured recipe from a webpage. The page may be in Hebrew or English. Return ONLY valid JSON (no markdown fences, no commentary), matching exactly this schema:

{{
  "title": "the recipe name, in the page's original language (usually Hebrew)",
  "category": "ONE of: desserts, pastries, bread, meat, fish, salads, pasta, soups, stews, breakfast, drinks, other",
  "ingredients": "multiline string. One ingredient per line, prefixed with '• '. Group sections with a heading like 'לבצק:' on its own line. Keep quantities exactly as written.",
  "instructions": "multiline string. Numbered steps (1. 2. 3.) one per line. Keep all details (times, temperatures, techniques).",
  "notes": "short notes: serving size, prep/cook time, dietary tags (e.g. ללא גלוטן), and any tips the author calls out. Empty string if none."
}}

Category guidance:
- desserts: cakes, tiramisu, mousse, ice cream
- pastries: cookies, jam squares, rugelach, scones, sweet pastries, sweet bars
- bread: pita, challah, focaccia, savory breads
- meat: any meat/poultry main dish
- fish: any fish/seafood main
- salads: salads as a main course
- pasta: pasta, rice, grain bowls as main
- soups: soups, thin stews
- stews: thick stews, casseroles, gratins, savory bakes
- breakfast: pancakes, eggs, granola
- drinks: smoothies, cocktails, lemonades
- other: anything else (sauces, spreads, jams)

Rules:
- If a field cannot be found, use an empty string for that field (or "other" for category).
- Do NOT invent quantities or steps. Pull only what's in the text.
- Output must be parseable JSON. No trailing commas. No code fences.

Page title: {page_title!r}
Source URL: {url}

Page content:
---
{page_text}
---

Return the JSON object now:"""


def parse_json_loose(text: str) -> dict:
    if not text:
        raise ValueError("empty response")
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned)
        cleaned = re.sub(r"\s*```\s*$", "", cleaned)
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        pass
    start = cleaned.find("{")
    end = cleaned.rfind("}")
    if start >= 0 and end > start:
        return json.loads(cleaned[start:end + 1])
    raise ValueError(f"could not parse JSON from: {text[:200]}")


def normalize_extracted(d: dict) -> ExtractedRecipe:
    cat = (d.get("category") or "other").strip().lower()
    if cat not in VALID_CATEGORIES:
        cat = "other"
    return ExtractedRecipe(
        title=(d.get("title") or "").strip(),
        category=cat,
        ingredients=(d.get("ingredients") or "").strip(),
        instructions=(d.get("instructions") or "").strip(),
        notes=(d.get("notes") or "").strip(),
    )


# --- Anthropic -------------------------------------------------------------

async def extract_anthropic(page_text: str, page_title: str, url: str) -> ProviderResult:
    started = time.monotonic()
    if not settings.anthropic_api_key:
        return ProviderResult(provider="anthropic", success=False, error="API key not configured")
    try:
        prompt = build_prompt(page_text, page_title, url)
        async with httpx.AsyncClient(timeout=settings.llm_timeout_seconds) as client:
            resp = await client.post(
                "https://api.anthropic.com/v1/messages",
                headers={
                    "x-api-key": settings.anthropic_api_key,
                    "anthropic-version": "2023-06-01",
                    "content-type": "application/json",
                },
                json={
                    "model": settings.anthropic_model,
                    "max_tokens": 2500,
                    "messages": [{"role": "user", "content": prompt}],
                },
            )
            resp.raise_for_status()
            payload = resp.json()
        text = "".join(b.get("text", "") for b in payload.get("content", []) if b.get("type") == "text")
        data = parse_json_loose(text)
        elapsed = int((time.monotonic() - started) * 1000)
        return ProviderResult(provider="anthropic", success=True,
                              data=normalize_extracted(data), elapsed_ms=elapsed)
    except httpx.HTTPStatusError as e:
        body = e.response.text[:200] if e.response is not None else ""
        return ProviderResult(provider="anthropic", success=False,
                              error=f"HTTP {e.response.status_code}: {body}",
                              elapsed_ms=int((time.monotonic() - started) * 1000))
    except Exception as e:
        return ProviderResult(provider="anthropic", success=False,
                              error=f"{type(e).__name__}: {e}",
                              elapsed_ms=int((time.monotonic() - started) * 1000))


# --- OpenAI ----------------------------------------------------------------

async def extract_openai(page_text: str, page_title: str, url: str) -> ProviderResult:
    started = time.monotonic()
    if not settings.openai_api_key:
        return ProviderResult(provider="openai", success=False, error="API key not configured")
    try:
        prompt = build_prompt(page_text, page_title, url)
        async with httpx.AsyncClient(timeout=settings.llm_timeout_seconds) as client:
            resp = await client.post(
                f"{settings.openai_base_url}/chat/completions",
                headers={
                    "Authorization": f"Bearer {settings.openai_api_key}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": settings.openai_model,
                    "messages": [{"role": "user", "content": prompt}],
                    "response_format": {"type": "json_object"},
                    "temperature": 0.2,
                },
            )
            resp.raise_for_status()
            payload = resp.json()
        text = payload["choices"][0]["message"]["content"]
        data = parse_json_loose(text)
        elapsed = int((time.monotonic() - started) * 1000)
        return ProviderResult(provider="openai", success=True,
                              data=normalize_extracted(data), elapsed_ms=elapsed)
    except httpx.HTTPStatusError as e:
        body = e.response.text[:200] if e.response is not None else ""
        return ProviderResult(provider="openai", success=False,
                              error=f"HTTP {e.response.status_code}: {body}",
                              elapsed_ms=int((time.monotonic() - started) * 1000))
    except Exception as e:
        return ProviderResult(provider="openai", success=False,
                              error=f"{type(e).__name__}: {e}",
                              elapsed_ms=int((time.monotonic() - started) * 1000))


# --- dispatch --------------------------------------------------------------

PROVIDER_MAP = {
    "anthropic": extract_anthropic,
    "openai": extract_openai,
}


def list_providers() -> list[ProviderInfo]:
    return [
        ProviderInfo(id="anthropic", name="Claude (Anthropic)",
                     model=settings.anthropic_model,
                     enabled=bool(settings.anthropic_api_key)),
        ProviderInfo(id="openai", name="GPT (OpenAI)",
                     model=settings.openai_model,
                     enabled=bool(settings.openai_api_key)),
    ]


async def extract_with_providers(page_text: str, page_title: str, url: str,
                                 providers: list[str]) -> list[ProviderResult]:
    tasks = []
    for p in providers:
        fn = PROVIDER_MAP.get(p)
        if fn is None:
            tasks.append(asyncio.sleep(0, result=ProviderResult(
                provider=p, success=False, error="Unknown provider")))
        else:
            tasks.append(fn(page_text, page_title, url))
    return await asyncio.gather(*tasks)

# ---------------------------------------------------------------------------
# Vision extraction (PDF pages / uploaded images)
# ---------------------------------------------------------------------------

VISION_PROMPT = """You are extracting a recipe from a photo or scanned document image. Read carefully — the text may be handwritten, printed, or photographed at an angle.

Return ONLY valid JSON (no markdown fences, no commentary):
{
  "title": "recipe name in its original language",
  "category": "ONE of: desserts, pastries, bread, meat, fish, salads, pasta, soups, stews, breakfast, drinks, other",
  "ingredients": "one ingredient per line, prefixed with '• '. Keep exact quantities and units.",
  "instructions": "numbered steps (1. 2. 3.) one per line. Keep all times and temperatures.",
  "notes": "serving size, prep/cook time, dietary tags, tips. Empty string if none."
}

If a field is not visible in the image, use an empty string. Return only the JSON object."""


async def extract_anthropic_vision(image_pages: list[bytes], filename: str) -> ProviderResult:
    started = time.monotonic()
    if not settings.anthropic_api_key:
        return ProviderResult(provider="anthropic", success=False, error="API key not configured")
    try:
        content = []
        for page_bytes in image_pages[:4]:  # max 4 pages to stay within context
            b64 = base64.b64encode(page_bytes).decode()
            content.append({"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": b64}})
        content.append({"type": "text", "text": VISION_PROMPT})

        async with httpx.AsyncClient(timeout=settings.llm_timeout_seconds) as client:
            resp = await client.post(
                "https://api.anthropic.com/v1/messages",
                headers={
                    "x-api-key": settings.anthropic_api_key,
                    "anthropic-version": "2023-06-01",
                    "content-type": "application/json",
                },
                json={
                    "model": settings.anthropic_model,
                    "max_tokens": 2500,
                    "messages": [{"role": "user", "content": content}],
                },
            )
            resp.raise_for_status()
            payload = resp.json()
        text = "".join(b.get("text", "") for b in payload.get("content", []) if b.get("type") == "text")
        data = parse_json_loose(text)
        elapsed = int((time.monotonic() - started) * 1000)
        return ProviderResult(provider="anthropic", success=True,
                              data=normalize_extracted(data), elapsed_ms=elapsed)
    except httpx.HTTPStatusError as e:
        body = e.response.text[:200] if e.response is not None else ""
        return ProviderResult(provider="anthropic", success=False,
                              error=f"HTTP {e.response.status_code}: {body}",
                              elapsed_ms=int((time.monotonic() - started) * 1000))
    except Exception as e:
        return ProviderResult(provider="anthropic", success=False,
                              error=f"{type(e).__name__}: {e}",
                              elapsed_ms=int((time.monotonic() - started) * 1000))


async def extract_openai_vision(image_pages: list[bytes], filename: str) -> ProviderResult:
    started = time.monotonic()
    if not settings.openai_api_key:
        return ProviderResult(provider="openai", success=False, error="API key not configured")
    try:
        content = []
        for page_bytes in image_pages[:4]:
            b64 = base64.b64encode(page_bytes).decode()
            content.append({"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64}", "detail": "high"}})
        content.append({"type": "text", "text": VISION_PROMPT})

        async with httpx.AsyncClient(timeout=settings.llm_timeout_seconds) as client:
            resp = await client.post(
                f"{settings.openai_base_url}/chat/completions",
                headers={
                    "Authorization": f"Bearer {settings.openai_api_key}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": settings.openai_model,
                    "messages": [{"role": "user", "content": content}],
                    "temperature": 0.2,
                    # Note: response_format json_object is NOT used here because
                    # vision + json_object mode isn't supported by all OpenAI models
                },
            )
            resp.raise_for_status()
            payload = resp.json()
        text = payload["choices"][0]["message"]["content"]
        data = parse_json_loose(text)
        elapsed = int((time.monotonic() - started) * 1000)
        return ProviderResult(provider="openai", success=True,
                              data=normalize_extracted(data), elapsed_ms=elapsed)
    except httpx.HTTPStatusError as e:
        body = e.response.text[:200] if e.response is not None else ""
        return ProviderResult(provider="openai", success=False,
                              error=f"HTTP {e.response.status_code}: {body}",
                              elapsed_ms=int((time.monotonic() - started) * 1000))
    except Exception as e:
        return ProviderResult(provider="openai", success=False,
                              error=f"{type(e).__name__}: {e}",
                              elapsed_ms=int((time.monotonic() - started) * 1000))


VISION_PROVIDER_MAP = {
    "anthropic": extract_anthropic_vision,
    "openai": extract_openai_vision,
}


async def extract_with_providers_vision(image_pages: list[bytes], filename: str,
                                        providers: list[str]) -> list[ProviderResult]:
    tasks = []
    for p in providers:
        fn = VISION_PROVIDER_MAP.get(p)
        if fn is None:
            tasks.append(asyncio.sleep(0, result=ProviderResult(
                provider=p, success=False, error="Unknown provider")))
        else:
            tasks.append(fn(image_pages, filename))
    return await asyncio.gather(*tasks)
