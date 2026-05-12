"""LLM providers for recipe extraction.

Supported providers:
  gemini    — Google Gemini Flash (FREE tier, vision supported)
  groq      — Groq (FREE tier, vision via llama-3.2-vision)
  ollama    — Self-hosted Ollama (FREE, local, vision via llava/llama3.2-vision)
  openai    — OpenAI GPT (paid)
  anthropic — Anthropic Claude (paid, own API format)

Any provider with its key/URL configured is automatically enabled.
"""
import asyncio
import base64
import json
import time
import re
from dataclasses import dataclass

import httpx

from config import settings
from schemas import ProviderResult, ExtractedRecipe, ProviderInfo


VALID_CATEGORIES = {
    "desserts", "pastries", "bread", "meat", "fish", "salads",
    "pasta", "soups", "stews", "breakfast", "drinks", "other",
}


# ---------------------------------------------------------------------------
# Prompts
# ---------------------------------------------------------------------------

TEXT_PROMPT = """You are extracting a recipe from webpage text. Return ONLY valid JSON (no markdown fences):

{{
  "title": "recipe name in original language",
  "category": "ONE of: desserts, pastries, bread, meat, fish, salads, pasta, soups, stews, breakfast, drinks, other",
  "ingredients": "one ingredient per line prefixed with '• '. Group sections with headers like 'לבצק:'",
  "instructions": "numbered steps 1. 2. 3. one per line",
  "notes": "serving size, time, dietary tags, tips. Empty string if none."
}}

Source: {url}
Title hint: {title}

Content:
---
{text}
---

Return only the JSON:"""

VISION_PROMPT = """You are extracting a recipe from a photo or scanned document. Read carefully.

Return ONLY valid JSON (no markdown fences):
{
  "title": "recipe name in original language",
  "category": "ONE of: desserts, pastries, bread, meat, fish, salads, pasta, soups, stews, breakfast, drinks, other",
  "ingredients": "one ingredient per line prefixed with '• '",
  "instructions": "numbered steps 1. 2. 3.",
  "notes": "serving size, time, dietary tags, tips. Empty string if none."
}

Return only the JSON:"""


# ---------------------------------------------------------------------------
# Parsing helpers
# ---------------------------------------------------------------------------

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
    start, end = cleaned.find("{"), cleaned.rfind("}")
    if start >= 0 and end > start:
        return json.loads(cleaned[start:end + 1])
    raise ValueError(f"no JSON found in: {text[:200]}")


def normalize(d: dict) -> ExtractedRecipe:
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


# ---------------------------------------------------------------------------
# Provider registry
# ---------------------------------------------------------------------------

@dataclass
class Provider:
    id: str
    name: str
    base_url: str          # OpenAI-compatible base, or "anthropic"
    api_key: str
    text_model: str
    vision_model: str      # may differ from text_model (e.g. Groq)
    is_anthropic: bool = False


def _enabled_providers() -> list[Provider]:
    ps = []
    # Direct providers — tried first
    if settings.anthropic_api_key:
        ps.append(Provider(id="anthropic", name="Claude (Anthropic)",
            base_url="anthropic", api_key=settings.anthropic_api_key,
            text_model=settings.anthropic_model, vision_model=settings.anthropic_model,
            is_anthropic=True))
    if settings.openai_api_key:
        ps.append(Provider(id="openai", name="GPT (OpenAI)",
            base_url=settings.openai_base_url, api_key=settings.openai_api_key,
            text_model=settings.openai_model, vision_model=settings.openai_model))
    if settings.xai_api_key:
        ps.append(Provider(id="xai", name="Grok (xAI)",
            base_url="https://api.x.ai/v1", api_key=settings.xai_api_key,
            text_model=settings.xai_model, vision_model=settings.xai_vision_model))
    if settings.gemini_api_key:
        ps.append(Provider(id="gemini", name="Gemini (Google)",
            base_url="https://generativelanguage.googleapis.com/v1beta/openai",
            api_key=settings.gemini_api_key,
            text_model=settings.gemini_model, vision_model=settings.gemini_model))
    if settings.groq_api_key:
        ps.append(Provider(id="groq", name="Groq",
            base_url="https://api.groq.com/openai/v1", api_key=settings.groq_api_key,
            text_model=settings.groq_model, vision_model=settings.groq_vision_model))
    # Fallback providers
    if settings.openrouter_api_key:
        ps.append(Provider(id="openrouter", name="OpenRouter (fallback)",
            base_url="https://openrouter.ai/api/v1", api_key=settings.openrouter_api_key,
            text_model=settings.openrouter_text_model, vision_model=settings.openrouter_vision_model))
    return ps
def _provider_by_id(pid: str) -> Provider | None:
    for p in _enabled_providers():
        if p.id == pid:
            return p
    return None


def list_providers() -> list[ProviderInfo]:
    all_defs = [
        ("anthropic",   "Claude (Anthropic)",        settings.anthropic_model,        bool(settings.anthropic_api_key)),
        ("openai",      "GPT (OpenAI)",               settings.openai_model,           bool(settings.openai_api_key)),
        ("xai",         "Grok (xAI)",                 settings.xai_model,              bool(settings.xai_api_key)),
        ("gemini",      "Gemini (Google)",             settings.gemini_model,           bool(settings.gemini_api_key)),
        ("groq",        "Groq",                        settings.groq_model,             bool(settings.groq_api_key)),
        ("openrouter",  "OpenRouter — fallback",       settings.openrouter_text_model,  bool(settings.openrouter_api_key)),
    ]
    return [ProviderInfo(id=i, name=n, model=m, enabled=e) for i, n, m, e in all_defs]

# ---------------------------------------------------------------------------
# OpenAI-compatible extraction (text)
# ---------------------------------------------------------------------------

async def _extract_compat_text(provider: Provider, text: str, title: str, url: str) -> ProviderResult:
    started = time.monotonic()
    prompt = TEXT_PROMPT.format(text=text[:18000], title=title, url=url or "")
    try:
        async with httpx.AsyncClient(timeout=settings.llm_timeout_seconds) as client:
            resp = await client.post(
                f"{provider.base_url}/chat/completions",
                headers={"Authorization": f"Bearer {provider.api_key}", "Content-Type": "application/json"},
                json={
                    "model": provider.text_model,
                    "messages": [{"role": "user", "content": prompt}],
                    "temperature": 0.2,
                },
            )
            resp.raise_for_status()
        raw = resp.json()["choices"][0]["message"]["content"]
        return ProviderResult(provider=provider.id, success=True,
                              data=normalize(parse_json_loose(raw)),
                              elapsed_ms=int((time.monotonic() - started) * 1000))
    except httpx.HTTPStatusError as e:
        body = e.response.text[:300] if e.response is not None else ""
        return ProviderResult(provider=provider.id, success=False,
                              error=f"HTTP {e.response.status_code}: {body}",
                              elapsed_ms=int((time.monotonic() - started) * 1000))
    except Exception as e:
        return ProviderResult(provider=provider.id, success=False,
                              error=f"{type(e).__name__}: {e}",
                              elapsed_ms=int((time.monotonic() - started) * 1000))


# ---------------------------------------------------------------------------
# OpenAI-compatible extraction (vision)
# ---------------------------------------------------------------------------

async def _extract_compat_vision(provider: Provider, images: list[bytes]) -> ProviderResult:
    started = time.monotonic()
    try:
        content = []
        for img in images[:4]:
            b64 = base64.b64encode(img).decode()
            content.append({"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64}", "detail": "high"}})
        content.append({"type": "text", "text": VISION_PROMPT})

        async with httpx.AsyncClient(timeout=settings.llm_timeout_seconds) as client:
            resp = await client.post(
                f"{provider.base_url}/chat/completions",
                headers={"Authorization": f"Bearer {provider.api_key}", "Content-Type": "application/json"},
                json={
                    "model": provider.vision_model,
                    "messages": [{"role": "user", "content": content}],
                    "temperature": 0.2,
                },
            )
            resp.raise_for_status()
        raw = resp.json()["choices"][0]["message"]["content"]
        return ProviderResult(provider=provider.id, success=True,
                              data=normalize(parse_json_loose(raw)),
                              elapsed_ms=int((time.monotonic() - started) * 1000))
    except httpx.HTTPStatusError as e:
        body = e.response.text[:300] if e.response is not None else ""
        return ProviderResult(provider=provider.id, success=False,
                              error=f"HTTP {e.response.status_code}: {body}",
                              elapsed_ms=int((time.monotonic() - started) * 1000))
    except Exception as e:
        return ProviderResult(provider=provider.id, success=False,
                              error=f"{type(e).__name__}: {e}",
                              elapsed_ms=int((time.monotonic() - started) * 1000))


# ---------------------------------------------------------------------------
# Anthropic extraction (text)
# ---------------------------------------------------------------------------

async def _extract_anthropic_text(provider: Provider, text: str, title: str, url: str) -> ProviderResult:
    started = time.monotonic()
    prompt = TEXT_PROMPT.format(text=text[:18000], title=title, url=url or "")
    try:
        async with httpx.AsyncClient(timeout=settings.llm_timeout_seconds) as client:
            resp = await client.post(
                "https://api.anthropic.com/v1/messages",
                headers={"x-api-key": provider.api_key, "anthropic-version": "2023-06-01",
                         "content-type": "application/json"},
                json={"model": provider.text_model, "max_tokens": 2500,
                      "messages": [{"role": "user", "content": prompt}]},
            )
            resp.raise_for_status()
        payload = resp.json()
        raw = "".join(b.get("text", "") for b in payload.get("content", []) if b.get("type") == "text")
        return ProviderResult(provider=provider.id, success=True,
                              data=normalize(parse_json_loose(raw)),
                              elapsed_ms=int((time.monotonic() - started) * 1000))
    except httpx.HTTPStatusError as e:
        body = e.response.text[:300] if e.response is not None else ""
        return ProviderResult(provider=provider.id, success=False,
                              error=f"HTTP {e.response.status_code}: {body}",
                              elapsed_ms=int((time.monotonic() - started) * 1000))
    except Exception as e:
        return ProviderResult(provider=provider.id, success=False,
                              error=f"{type(e).__name__}: {e}",
                              elapsed_ms=int((time.monotonic() - started) * 1000))


# ---------------------------------------------------------------------------
# Anthropic extraction (vision)
# ---------------------------------------------------------------------------

async def _extract_anthropic_vision(provider: Provider, images: list[bytes]) -> ProviderResult:
    started = time.monotonic()
    try:
        content = []
        for img in images[:4]:
            b64 = base64.b64encode(img).decode()
            content.append({"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": b64}})
        content.append({"type": "text", "text": VISION_PROMPT})

        async with httpx.AsyncClient(timeout=settings.llm_timeout_seconds) as client:
            resp = await client.post(
                "https://api.anthropic.com/v1/messages",
                headers={"x-api-key": provider.api_key, "anthropic-version": "2023-06-01",
                         "content-type": "application/json"},
                json={"model": provider.vision_model, "max_tokens": 2500,
                      "messages": [{"role": "user", "content": content}]},
            )
            resp.raise_for_status()
        payload = resp.json()
        raw = "".join(b.get("text", "") for b in payload.get("content", []) if b.get("type") == "text")
        return ProviderResult(provider=provider.id, success=True,
                              data=normalize(parse_json_loose(raw)),
                              elapsed_ms=int((time.monotonic() - started) * 1000))
    except httpx.HTTPStatusError as e:
        body = e.response.text[:300] if e.response is not None else ""
        return ProviderResult(provider=provider.id, success=False,
                              error=f"HTTP {e.response.status_code}: {body}",
                              elapsed_ms=int((time.monotonic() - started) * 1000))
    except Exception as e:
        return ProviderResult(provider=provider.id, success=False,
                              error=f"{type(e).__name__}: {e}",
                              elapsed_ms=int((time.monotonic() - started) * 1000))


# ---------------------------------------------------------------------------
# Dispatch
# ---------------------------------------------------------------------------

async def _run_text(pid: str, text: str, title: str, url: str) -> ProviderResult:
    p = _provider_by_id(pid)
    if p is None:
        return ProviderResult(provider=pid, success=False, error="Provider not enabled or unknown")
    if p.is_anthropic:
        return await _extract_anthropic_text(p, text, title, url)
    return await _extract_compat_text(p, text, title, url)


async def _run_vision(pid: str, images: list[bytes]) -> ProviderResult:
    p = _provider_by_id(pid)
    if p is None:
        return ProviderResult(provider=pid, success=False, error="Provider not enabled or unknown")
    if p.is_anthropic:
        return await _extract_anthropic_vision(p, images)
    return await _extract_compat_vision(p, images)


async def extract_with_providers(text: str, title: str, url: str,
                                 providers: list[str]) -> list[ProviderResult]:
    return await asyncio.gather(*[_run_text(pid, text, title, url) for pid in providers])


async def extract_with_providers_vision(images: list[bytes], _filename: str,
                                        providers: list[str]) -> list[ProviderResult]:
    return await asyncio.gather(*[_run_vision(pid, images) for pid in providers])


# ---------------------------------------------------------------------------
# Fallback mode — sequential, stops at first success
# ---------------------------------------------------------------------------

async def extract_with_fallback(text: str, title: str, url: str,
                                providers: list[str]) -> list[ProviderResult]:
    """Try each configured provider in order. Skip unconfigured ones silently.
    Stop at first success."""
    results = []
    for pid in providers:
        p = _provider_by_id(pid)
        if p is None:
            continue  # not configured — skip silently, don't add to results
        if p.is_anthropic:
            result = await _extract_anthropic_text(p, text, title, url)
        else:
            result = await _extract_compat_text(p, text, title, url)
        results.append(result)
        if result.success:
            break
    if not results:
        results.append(ProviderResult(provider="none", success=False,
                                      error="אין ספק LLM מוגדר. הגדר OLLAMA_BASE_URL או OPENROUTER_API_KEY."))
    return results


async def extract_with_fallback_vision(images: list[bytes],
                                       providers: list[str]) -> list[ProviderResult]:
    results = []
    for pid in providers:
        p = _provider_by_id(pid)
        if p is None:
            continue
        if p.is_anthropic:
            result = await _extract_anthropic_vision(p, images)
        else:
            result = await _extract_compat_vision(p, images)
        results.append(result)
        if result.success:
            break
    if not results:
        results.append(ProviderResult(provider="none", success=False,
                                      error="אין ספק LLM מוגדר. הגדר OLLAMA_BASE_URL או OPENROUTER_API_KEY."))
    return results
