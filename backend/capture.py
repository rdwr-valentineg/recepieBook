"""Render a URL to PDF + full-page screenshot using a shared Playwright browser.

A single Chromium instance is launched at startup and reused for all captures.
Each capture opens its own browser context (cookies, storage) for isolation.
"""
import asyncio
import os
from dataclasses import dataclass
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
                        "--disable-features=IsolateOrigins,site-per-process",
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


# Stealth: hide all common bot-detection signals
STEALTH_SCRIPT = """() => {
    Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
    Object.defineProperty(navigator, 'plugins', { get: () => [1, 2, 3, 4, 5] });
    Object.defineProperty(navigator, 'languages', { get: () => ['he-IL', 'he', 'en-US', 'en'] });
    if (!window.chrome) window.chrome = { runtime: {}, loadTimes: () => {}, csi: () => {} };
    const orig = window.navigator.permissions.query;
    window.navigator.permissions.query = (p) =>
        p.name === 'notifications'
            ? Promise.resolve({ state: Notification.permission })
            : orig(p);
}"""

# Cookie-banner / GDPR selectors to dismiss
DISMISS_SELECTORS = [
    'button:has-text("הסכמה")',
    'button:has-text("מקבל")',
    'button:has-text("אישור")',
    'button:has-text("Accept all")',
    'button:has-text("Accept")',
    'button:has-text("I agree")',
    'button:has-text("Got it")',
    '[id*="accept" i]',
    '[class*="accept-all" i]',
    '[class*="cookie-accept" i]',
]


async def _load_page(page, url: str) -> None:
    """Load a page with networkidle fallback, stealth, cookie dismiss, and lazy-load scroll."""
    # Inject stealth before page load
    await page.add_init_script(STEALTH_SCRIPT)

    try:
        await page.goto(url, wait_until="networkidle",
                        timeout=settings.capture_timeout_seconds * 1000)
    except PWTimeout:
        await page.wait_for_load_state("domcontentloaded")

    # Dismiss cookie banners
    for sel in DISMISS_SELECTORS:
        try:
            btn = page.locator(sel).first
            if await btn.is_visible(timeout=500):
                await btn.click(timeout=1500)
                await page.wait_for_timeout(300)
                break
        except Exception:
            pass

    # Scroll through the page in steps to trigger lazy loading
    await page.evaluate("""async () => {
        const delay = ms => new Promise(r => setTimeout(r, ms));
        const total = document.body.scrollHeight;
        const step = Math.max(600, Math.floor(total / 6));
        for (let y = 0; y < total; y += step) {
            window.scrollTo(0, y);
            await delay(200);
        }
        window.scrollTo(0, 0);
        await delay(400);
    }""")

    # Final settle time
    await page.wait_for_timeout(600)


async def capture_url(url: str) -> CaptureResult:
    """Open URL in chromium, return HTML + PDF bytes + screenshot bytes.

    Strategy:
      1. Try the URL as-is.
      2. If content looks thin (< 500 chars after trafilatura), try appending /print
         or ?print=1 as many recipe sites expose a clean print view.
    """
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
    try:
        await _load_page(page, url)

        final_url = page.url
        html = await page.content()

        # Quick content-length check: if very thin, try print view
        from scraper import clean_html_to_text
        _, text_preview = clean_html_to_text(html)
        if len(text_preview.strip()) < 500:
            for print_url in [url.rstrip('/') + '/print', url + ('&' if '?' in url else '?') + 'print=1']:
                try:
                    await _load_page(page, print_url)
                    candidate_html = await page.content()
                    _, candidate_text = clean_html_to_text(candidate_html)
                    if len(candidate_text.strip()) > len(text_preview.strip()):
                        html = candidate_html
                        final_url = page.url
                        break
                except Exception:
                    pass

        screenshot_bytes = await page.screenshot(full_page=True, type="jpeg", quality=85)
        pdf_bytes = await page.pdf(
            format="A4",
            print_background=True,
            margin={"top": "12mm", "bottom": "12mm", "left": "10mm", "right": "10mm"},
        )

        return CaptureResult(html=html, pdf_bytes=pdf_bytes,
                              screenshot_bytes=screenshot_bytes, final_url=final_url)
    except Exception as e:
        return CaptureResult(html="", pdf_bytes=None, screenshot_bytes=None,
                              final_url=url, error=f"{type(e).__name__}: {e}")
    finally:
        try:
            await ctx.close()
        except Exception:
            pass
async def capture_from_fetched_html(url: str) -> CaptureResult:
    """Fetch HTML with plain httpx first, then render with Playwright.

    Avoids CAPTCHA because Playwright never navigates to the URL —
    it just renders the HTML we already have. Works for server-rendered
    recipe sites (mako, krutit, foody, etc.) where content is in the HTML.
    """
    import httpx as _httpx
    try:
        async with _httpx.AsyncClient(
            timeout=settings.fetch_timeout_seconds,
            follow_redirects=True,
            headers={
                "User-Agent": (
                    "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_0) AppleWebKit/537.36 "
                    "(KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36"
                ),
                "Accept": "text/html,application/xhtml+xml;q=0.9,*/*;q=0.8",
                "Accept-Language": "he-IL,he;q=0.9,en;q=0.8",
                "Referer": "https://www.google.com/",
            },
        ) as client:
            resp = await client.get(url)
            resp.raise_for_status()
            html = resp.text
            final_url = str(resp.url)
    except Exception as e:
        return CaptureResult(html="", pdf_bytes=None, screenshot_bytes=None,
                              final_url=url, error=f"fetch failed: {e}")

    browser = await PlaywrightHolder.get_browser()
    ctx = await browser.new_context(
        viewport={"width": 1280, "height": 1800},
        locale="he-IL",
    )
    page = await ctx.new_page()
    try:
        await page.add_init_script(STEALTH_SCRIPT)
        # Render the pre-fetched HTML — base_url makes relative links resolve correctly
        try:
            await page.set_content(html, base_url=final_url, wait_until="networkidle",
                                    timeout=settings.capture_timeout_seconds * 1000)
        except PWTimeout:
            pass  # content may have loaded enough

        # Scroll to trigger lazy-loaded images/content
        await page.evaluate("""async () => {
            const delay = ms => new Promise(r => setTimeout(r, ms));
            const total = document.body.scrollHeight;
            const step = Math.max(600, Math.floor(total / 6));
            for (let y = 0; y < total; y += step) {
                window.scrollTo(0, y);
                await delay(150);
            }
            window.scrollTo(0, 0);
            await delay(300);
        }""")
        await page.wait_for_timeout(400)

        screenshot_bytes = await page.screenshot(full_page=True, type="jpeg", quality=85)
        pdf_bytes = await page.pdf(
            format="A4",
            print_background=True,
            margin={"top": "12mm", "bottom": "12mm", "left": "10mm", "right": "10mm"},
        )
        return CaptureResult(html=html, pdf_bytes=pdf_bytes,
                              screenshot_bytes=screenshot_bytes, final_url=final_url)
    except Exception as e:
        return CaptureResult(html="", pdf_bytes=None, screenshot_bytes=None,
                              final_url=url, error=f"render failed: {e}")
    finally:
        try:
            await ctx.close()
        except Exception:
            pass




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

    try:
        os.rmdir(src)
    except OSError:
        pass

    return pdf_name, screen_name


def cleanup_orphan_sessions(max_age_seconds: int = 3600) -> int:
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
            if now - os.path.getmtime(path) > max_age_seconds:
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
