"""Render a URL to PDF + full-page screenshot using a shared Playwright browser.

A single Chromium instance is launched at startup and reused for all captures.
Each capture opens its own browser context (cookies, storage) for isolation.
"""
import asyncio
import os
from dataclasses import dataclass, field
from typing import Optional

from playwright.async_api import async_playwright, Browser, Playwright, TimeoutError as PWTimeout

from config import settings


@dataclass
class CaptureResult:
    html: str
    pdf_bytes: Optional[bytes]
    screenshot_bytes: Optional[bytes]
    final_url: str
    error: Optional[str] = None


class PlaywrightHolder:
    """Process-wide Playwright + Chromium, started/stopped with the app."""
    _lock = asyncio.Lock()
    _pw: Optional[Playwright] = None
    _browser: Optional[Browser] = None

    @classmethod
    async def get_browser(cls) -> Browser:
        async with cls._lock:
            if cls._browser is None or not cls._browser.is_connected():
                cls._pw = await async_playwright().start()
                cls._browser = await cls._pw.chromium.launch(
                    headless=True,
                    args=[
                        "--disable-blink-features=AutomationControlled",
                        "--no-sandbox",
                    ],
                )
            return cls._browser

    @classmethod
    async def close(cls):
        async with cls._lock:
            if cls._browser:
                try:
                    await cls._browser.close()
                except Exception:
                    pass
                cls._browser = None
            if cls._pw:
                try:
                    await cls._pw.stop()
                except Exception:
                    pass
                cls._pw = None


# Common cookie-banner / GDPR / age-gate selectors we'll try to dismiss
DISMISS_SELECTORS = [
    'button:has-text("הסכמה")',
    'button:has-text("מקבל")',
    'button:has-text("אישור")',
    'button:has-text("Accept")',
    'button:has-text("Accept all")',
    'button:has-text("I agree")',
    'button:has-text("Agree")',
    'button:has-text("Got it")',
    '[id*="accept" i]',
    '[class*="accept-all" i]',
    '[class*="cookie-accept" i]',
]


async def capture_url(url: str) -> CaptureResult:
    """Open URL in chromium, return HTML + PDF bytes + screenshot bytes."""
    browser = await PlaywrightHolder.get_browser()
    ctx = await browser.new_context(
        viewport={"width": 1280, "height": 1800},
        locale="he-IL",
        user_agent=(
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_0) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36"
        ),
    )
    page = await ctx.new_page()
    error: Optional[str] = None
    try:
        try:
            await page.goto(url, wait_until="networkidle",
                            timeout=settings.capture_timeout_seconds * 1000)
        except PWTimeout:
            # Some sites never go fully idle (long-polling, analytics). Fall back to DOM ready.
            await page.wait_for_load_state("domcontentloaded")

        # Best-effort dismissal of cookie banners (quick, ignore failures)
        for sel in DISMISS_SELECTORS:
            try:
                btn = page.locator(sel).first
                if await btn.is_visible(timeout=500):
                    await btn.click(timeout=1500)
                    await page.wait_for_timeout(200)
                    break
            except Exception:
                pass

        # Let any post-click reflows settle
        await page.wait_for_timeout(500)

        final_url = page.url
        html = await page.content()

        # Screenshot full page as JPEG (smaller than PNG, plenty good for archival)
        screenshot_bytes = await page.screenshot(full_page=True, type="jpeg", quality=82)

        # PDF (chromium only, headless)
        pdf_bytes = await page.pdf(
            format="A4",
            print_background=True,
            margin={"top": "12mm", "bottom": "12mm", "left": "10mm", "right": "10mm"},
        )

        return CaptureResult(
            html=html,
            pdf_bytes=pdf_bytes,
            screenshot_bytes=screenshot_bytes,
            final_url=final_url,
        )
    except Exception as e:
        error = f"{type(e).__name__}: {e}"
        return CaptureResult(html="", pdf_bytes=None, screenshot_bytes=None,
                              final_url=url, error=error)
    finally:
        try:
            await ctx.close()
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Session storage for in-flight captures (between /extract and /recipes)
# ---------------------------------------------------------------------------

def session_dir(session_id: str) -> str:
    return os.path.join(settings.data_dir, "sessions", session_id)


def recipe_capture_dir(recipe_id: str) -> str:
    return os.path.join(settings.data_dir, "captures", recipe_id)


def save_session_capture(session_id: str, result: CaptureResult) -> None:
    d = session_dir(session_id)
    os.makedirs(d, exist_ok=True)
    if result.pdf_bytes:
        with open(os.path.join(d, "page.pdf"), "wb") as f:
            f.write(result.pdf_bytes)
    if result.screenshot_bytes:
        with open(os.path.join(d, "page.jpg"), "wb") as f:
            f.write(result.screenshot_bytes)


def promote_session_to_recipe(session_id: str, recipe_id: str) -> tuple[Optional[str], Optional[str]]:
    """
    Move PDF and screenshot from temp session dir to the recipe's permanent dir.
    Returns (pdf_filename, screenshot_filename).
    """
    src = session_dir(session_id)
    if not os.path.isdir(src):
        return None, None
    dst = recipe_capture_dir(recipe_id)
    os.makedirs(dst, exist_ok=True)

    pdf_name = None
    screen_name = None

    pdf_src = os.path.join(src, "page.pdf")
    if os.path.isfile(pdf_src):
        pdf_name = "page.pdf"
        os.replace(pdf_src, os.path.join(dst, pdf_name))

    screen_src = os.path.join(src, "page.jpg")
    if os.path.isfile(screen_src):
        screen_name = "page.jpg"
        os.replace(screen_src, os.path.join(dst, screen_name))

    # Best-effort cleanup
    try:
        os.rmdir(src)
    except OSError:
        pass

    return pdf_name, screen_name


def cleanup_orphan_sessions(max_age_seconds: int = 3600) -> int:
    """Remove session dirs older than max_age. Returns number of dirs removed."""
    import time
    base = os.path.join(settings.data_dir, "sessions")
    if not os.path.isdir(base):
        return 0
    removed = 0
    now = time.time()
    for name in os.listdir(base):
        path = os.path.join(base, name)
        if not os.path.isdir(path):
            continue
        try:
            age = now - os.path.getmtime(path)
            if age > max_age_seconds:
                for f in os.listdir(path):
                    try:
                        os.remove(os.path.join(path, f))
                    except OSError:
                        pass
                os.rmdir(path)
                removed += 1
        except OSError:
            pass
    return removed
