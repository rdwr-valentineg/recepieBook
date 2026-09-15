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
import re
import time
from dataclasses import dataclass

import httpx
from config import settings
from schemas import ExtractedRecipe, ProviderInfo, ProviderResult

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
  "ingredients": "multiline. If the recipe has sections (dough+filling, sauce+base etc.), use a section header ending with ':' (e.g. 'לבצק:', 'למילוי:', 'לרוטב:') before each group, then each ingredient on its own line prefixed with '• '.",
  "instructions": "numbered steps. If there are named stages (e.g. 'הכנת הבצק:'), include them as section headers before the relevant steps.",
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
  "ingredients": "multiline. Use section headers ending with ':' for separate groups (e.g. 'לבצק:', 'למילוי:'). Each ingredient prefixed with '• '.",
  "instructions": "numbered steps. Named stages as section headers.",
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
        text_models = [m.strip() for m in settings.openrouter_text_models.split(',') if m.strip()]
        vision_models = [m.strip() for m in settings.openrouter_vision_models.split(',') if m.strip()]
        for i, text_model in enumerate(text_models):
            vision_model = vision_models[i % len(vision_models)] if vision_models else text_model
            short = text_model.split('/')[-1].replace(':free', '')
            ps.append(Provider(
                id=f"openrouter_{i}",
                name=f"OpenRouter · {short}",
                base_url="https://openrouter.ai/api/v1",
                api_key=settings.openrouter_api_key,
                text_model=text_model,
                vision_model=vision_model,
            ))
    # Ollama — local, last resort, no internet needed. OpenAI-compatible API,
    # but it takes no API key (httpx still needs a non-empty Authorization
    # header value, so we send a placeholder).
    if settings.ollama_base_url:
        ps.append(Provider(id="ollama", name="Ollama (מקומי)",
            base_url=settings.ollama_base_url.rstrip("/"),
            api_key="ollama",
            text_model=settings.ollama_model,
            vision_model=settings.ollama_vision_model))
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
        ("openrouter",  "OpenRouter — fallback (auto model)",  "multiple :free models",  bool(settings.openrouter_api_key)),
        ("ollama",      "Ollama (מקומי)",              settings.ollama_model,           bool(settings.ollama_base_url)),
    ]
    return [ProviderInfo(id=i, name=n, model=m, enabled=e) for i, n, m, e in all_defs]

# ---------------------------------------------------------------------------
# OpenAI-compatible extraction (text)
# ---------------------------------------------------------------------------

async def _extract_compat_text(provider: Provider, text: str, title: str, url: str) -> ProviderResult:
    started = time.monotonic()
    prompt = TEXT_PROMPT.format(text=text[:40000], title=title, url=url or "")
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
        raw_resp = resp.json()
        msg = raw_resp["choices"][0]["message"]
        raw = msg.get("content") or msg.get("reasoning") or ""
        if not raw.strip():
            raise ValueError(
                f"empty response from {provider.text_model} "
                f"(finish_reason={raw_resp['choices'][0].get('finish_reason')})"
            )
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
        raw_resp = resp.json()
        msg = raw_resp["choices"][0]["message"]
        raw = msg.get("content") or msg.get("reasoning") or ""
        if not raw.strip():
            raise ValueError(
                f"empty response from {provider.text_model} "
                f"(finish_reason={raw_resp['choices'][0].get('finish_reason')})"
            )
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
    prompt = TEXT_PROMPT.format(text=text[:40000], title=title, url=url or "")
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

def _expand_providers(providers: list[str]) -> list[str]:
    """Expand the virtual 'openrouter' id into all configured openrouter_N providers."""
    result = []
    enabled_ids = {p.id for p in _enabled_providers()}
    for pid in providers:
        if pid == "openrouter":
            or_ids = sorted(i for i in enabled_ids if i.startswith("openrouter_"))
            result.extend(or_ids if or_ids else [])
        else:
            result.append(pid)
    return result


async def extract_with_fallback(text: str, title: str, url: str,
                                providers: list[str]) -> list[ProviderResult]:
    """Try each configured provider in order. Skip unconfigured ones silently.
    OpenRouter is automatically expanded into its per-model sub-providers.
    Stop at first success."""
    results = []
    for pid in _expand_providers(providers):
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
    for pid in _expand_providers(providers):
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


# ---------------------------------------------------------------------------
# Hebrew cleanup pass — short second call to fix text quality
# ---------------------------------------------------------------------------

HEBREW_CLEANUP_PROMPT = """You received recipe data extracted from a Hebrew website. Fix ONLY text quality issues:
- Garbled or broken Hebrew characters
- Incomplete/cut-off sentences
- Unnatural Hebrew phrasing from bad OCR or scraping
- Mixed RTL/LTR encoding artifacts
- Inconsistent number formatting (e.g. "1/2" vs "½")

Rules:
- Keep ALL field names exactly as-is
- Do NOT add or remove ingredients/steps
- Do NOT translate anything
- If the text is already fine, return it unchanged
- Return ONLY valid JSON, no markdown fences

Input:
{json}

Return the corrected JSON:"""


async def cleanup_hebrew(data: ExtractedRecipe, providers: list[str]) -> ExtractedRecipe:
    """Run a quick second LLM pass to fix Hebrew text quality."""
    import json as _json
    payload = {
        "title": data.title,
        "category": data.category,
        "ingredients": data.ingredients,
        "instructions": data.instructions,
        "notes": data.notes,
    }
    prompt = HEBREW_CLEANUP_PROMPT.format(json=_json.dumps(payload, ensure_ascii=False, indent=2))

    # Try providers in fallback order — use only the first success
    for pid in providers:
        p = _provider_by_id(pid)
        if p is None:
            continue
        try:
            if p.is_anthropic:
                async with httpx.AsyncClient(timeout=60) as client:
                    resp = await client.post(
                        "https://api.anthropic.com/v1/messages",
                        headers={"x-api-key": p.api_key, "anthropic-version": "2023-06-01",
                                 "content-type": "application/json"},
                        json={"model": p.text_model, "max_tokens": 2000,
                              "messages": [{"role": "user", "content": prompt}]},
                    )
                    resp.raise_for_status()
                    payload_resp = resp.json()
                    raw = "".join(b.get("text","") for b in payload_resp.get("content",[]) if b.get("type")=="text")
            else:
                async with httpx.AsyncClient(timeout=60) as client:
                    resp = await client.post(
                        f"{p.base_url}/chat/completions",
                        headers={"Authorization": f"Bearer {p.api_key}", "Content-Type": "application/json"},
                        json={"model": p.text_model,
                              "messages": [{"role": "user", "content": prompt}],
                              "temperature": 0.1},
                    )
                    resp.raise_for_status()
                    msg = resp.json()["choices"][0]["message"]
                    raw = msg.get("content") or msg.get("reasoning") or ""

            if raw.strip():
                fixed = parse_json_loose(raw)
                return normalize(fixed)
        except Exception:
            continue  # try next provider

    return data  # return original if all providers fail


# ---------------------------------------------------------------------------
# Hebrew enforcement — translate non-Hebrew recipes into Hebrew
# ---------------------------------------------------------------------------

_HEBREW_RE = re.compile(r"[\u0590-\u05FF]")
# Letters in any script — the denominator for the Hebrew ratio. Digits,
# punctuation and whitespace are script-neutral and would skew the result
# (an ingredient list is mostly numbers).
_LETTER_RE = re.compile(r"[^\W\d_]", re.UNICODE)


def hebrew_ratio(text: str) -> float:
    """Fraction of the letters in `text` that are Hebrew. 0.0 for no letters."""
    letters = _LETTER_RE.findall(text or "")
    if not letters:
        return 0.0
    hebrew = sum(1 for c in letters if _HEBREW_RE.match(c))
    return hebrew / len(letters)


def is_hebrew(data: ExtractedRecipe, threshold: float = 0.5) -> bool:
    """Is this recipe already in Hebrew?

    Judged on the body (ingredients + instructions) rather than the title —
    a Hebrew recipe often keeps an English or French dish name, and a recipe
    is not 'in English' because it's called 'Crème Brûlée'.
    """
    body = f"{data.ingredients}\n{data.instructions}"
    return hebrew_ratio(body) >= threshold


TRANSLATE_PROMPT = """Translate this recipe into Hebrew. It is for an \
Israeli home cook.

Rules:
- Translate title, ingredients, instructions and notes into natural, \
idiomatic Hebrew — the way an Israeli recipe would actually be written.
- Keep the SAME JSON structure and the same field names.
- Keep "category" exactly as-is — do not translate it, it is an internal id.
- Keep all numbers, quantities and units as they are. Convert unit NAMES to \
Hebrew (cup→כוס, tablespoon→כף, teaspoon→כפית, gram→גרם, ounce→אונקיה) but \
do NOT convert the values between measurement systems.
- Keep the section-header structure (lines ending with ':') and the '• ' \
ingredient prefixes.
- Do not add, remove, or reinterpret any ingredient or step.
- Leave brand names and proper nouns in their original form if they have no \
common Hebrew form.
- Return ONLY valid JSON, no markdown fences.

Input:
{json}

Return the translated JSON:"""


async def _call_provider_json(p: Provider, prompt: str, max_tokens: int = 3000) -> str:
    """Single text completion against one provider. Returns the raw string."""
    if p.is_anthropic:
        async with httpx.AsyncClient(timeout=settings.llm_timeout_seconds) as client:
            resp = await client.post(
                "https://api.anthropic.com/v1/messages",
                headers={"x-api-key": p.api_key, "anthropic-version": "2023-06-01",
                         "content-type": "application/json"},
                json={"model": p.text_model, "max_tokens": max_tokens,
                      "messages": [{"role": "user", "content": prompt}]},
            )
            resp.raise_for_status()
            payload = resp.json()
            return "".join(b.get("text", "") for b in payload.get("content", [])
                           if b.get("type") == "text")

    async with httpx.AsyncClient(timeout=settings.llm_timeout_seconds) as client:
        resp = await client.post(
            f"{p.base_url}/chat/completions",
            headers={"Authorization": f"Bearer {p.api_key}",
                     "Content-Type": "application/json"},
            json={"model": p.text_model,
                  "messages": [{"role": "user", "content": prompt}],
                  "temperature": 0.1},
        )
        resp.raise_for_status()
        msg = resp.json()["choices"][0]["message"]
        return msg.get("content") or msg.get("reasoning") or ""


async def translate_to_hebrew(data: ExtractedRecipe,
                              providers: list[str]) -> ExtractedRecipe:
    """Translate a recipe into Hebrew. Returns the original on total failure —
    a recipe in the wrong language beats no recipe at all."""
    import json as _json
    import logging
    logger = logging.getLogger("llm")

    payload = {
        "title": data.title,
        "category": data.category,
        "ingredients": data.ingredients,
        "instructions": data.instructions,
        "notes": data.notes,
    }
    prompt = TRANSLATE_PROMPT.format(
        json=_json.dumps(payload, ensure_ascii=False, indent=2))

    for pid in _expand_providers(providers):
        p = _provider_by_id(pid)
        if p is None:
            continue
        try:
            raw = await _call_provider_json(p, prompt)
            if not raw.strip():
                continue
            translated = normalize(parse_json_loose(raw))
            # Guard against a provider that echoed the input back untranslated
            # or returned something empty — either way, keep looking.
            if not translated.ingredients and not translated.instructions:
                continue
            if not is_hebrew(translated):
                logger.warning("translate_to_hebrew: %s returned non-Hebrew output", pid)
                continue
            # The model is told to leave category alone; enforce it anyway.
            translated.category = data.category
            logger.info("translate_to_hebrew: translated via %s", pid)
            return translated
        except Exception as e:
            logger.warning("translate_to_hebrew: %s failed: %s", pid, e)
            continue

    logger.warning("translate_to_hebrew: all providers failed, keeping original")
    return data


async def ensure_hebrew(data: ExtractedRecipe,
                        providers: list[str]) -> tuple[ExtractedRecipe, bool]:
    """Translate into Hebrew if it isn't already. Returns (recipe, translated)."""
    if is_hebrew(data):
        return data, False
    return await translate_to_hebrew(data, providers), True
