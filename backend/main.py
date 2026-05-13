"""Recipe Book — FastAPI application.

Routes:
  Auth:
    POST /api/auth/login        body: {password}
    POST /api/auth/logout
    GET  /api/auth/status

  Recipes (all require auth except /share routes):
    GET    /api/recipes
    POST   /api/recipes
    GET    /api/recipes/{id}
    PUT    /api/recipes/{id}
    DELETE /api/recipes/{id}
    POST   /api/recipes/{id}/image            multipart upload
    DELETE /api/recipes/{id}/image
    POST   /api/recipes/{id}/share            generate share token
    DELETE /api/recipes/{id}/share            revoke share token
    POST   /api/recipes/{id}/recapture        re-run capture from current URL
    GET    /api/recipes/{id}/pdf              the captured PDF
    GET    /api/recipes/{id}/screenshot       the captured screenshot
    GET    /api/images/{filename}             user-uploaded photo

  Categories:
    GET  /api/categories

  Extraction (auth):
    POST /api/extract                         {url, providers, capture}
    GET  /api/extract/session/{id}/pdf
    GET  /api/extract/session/{id}/screenshot
    GET  /api/providers

  Public share (NO auth):
    GET  /api/share/{token}                   recipe JSON
    GET  /api/share/{token}/pdf
    GET  /api/share/{token}/screenshot
    GET  /api/share/{token}/image

  Note: SPA routing is handled by the nginx frontend container.
        This backend is a pure API server.
"""
import asyncio
import os
import shutil
import uuid
from contextlib import asynccontextmanager
from typing import Optional, List
from fastapi import (
    FastAPI, Depends, HTTPException, Response, Cookie,
    UploadFile, File, Form
)
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session

from config import settings
from db import init_db, get_db, Recipe, new_share_token, session_scope
from schemas import (
    RecipeIn, RecipeUpdate, LoginRequest, ExtractRequest, ExtractResponse,
    CaptureInfo, ProvidersResponse, ShareResponse, Category
)
from auth import (
    require_auth, issue_session, clear_session, verify_password, is_authenticated,
)
from capture import (
    capture_url, capture_from_fetched_html, save_session_capture,
    promote_session_to_recipe, cleanup_orphan_sessions,
    recipe_capture_dir, PlaywrightHolder,
)
from scraper import clean_html_to_text, domain_of
from llm import (
    extract_with_providers, extract_with_providers_vision,
    extract_with_fallback, extract_with_fallback_vision,
    cleanup_hebrew, list_providers,
)
from seed_data import seed_if_empty


# ---------------------------------------------------------------------------
# CATEGORIES (kept in sync with the prompt)
# ---------------------------------------------------------------------------

CATEGORIES: List[Category] = [
    Category(id="desserts",  label="עוגות וקינוחים", emoji="🍰"),
    Category(id="pastries",  label="מאפים ועוגיות", emoji="🥧"),
    Category(id="bread",     label="לחמים",          emoji="🍞"),
    Category(id="meat",      label="בשר ועוף",       emoji="🍗"),
    Category(id="fish",      label="דגים",           emoji="🐟"),
    Category(id="salads",    label="סלטים",          emoji="🥗"),
    Category(id="pasta",     label="פסטה ואורז",     emoji="🍝"),
    Category(id="soups",     label="מרקים",          emoji="🍲"),
    Category(id="stews",     label="תבשילים",        emoji="🥘"),
    Category(id="breakfast", label="ארוחת בוקר",     emoji="🍳"),
    Category(id="drinks",    label="שתייה",          emoji="🥤"),
    Category(id="other",     label="שונות",          emoji="📌"),
]


# ---------------------------------------------------------------------------
# LIFESPAN
# ---------------------------------------------------------------------------

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Init DB + seed
    init_db()
    with session_scope() as db:
        n = seed_if_empty(db)
        if n:
            print(f"[init] seeded {n} recipes")

    # Background task: periodic session cleanup
    async def cleanup_loop():
        while True:
            try:
                await asyncio.sleep(15 * 60)
                removed = cleanup_orphan_sessions()
                if removed:
                    print(f"[cleanup] removed {removed} orphan capture sessions")
            except asyncio.CancelledError:
                break
            except Exception as e:
                print(f"[cleanup] error: {e}")

    cleanup_task = asyncio.create_task(cleanup_loop())

    try:
        yield
    finally:
        cleanup_task.cancel()
        try:
            await cleanup_task
        except asyncio.CancelledError:
            pass
        await PlaywrightHolder.close()


app = FastAPI(title="Recipe Book", lifespan=lifespan)


# ---------------------------------------------------------------------------
# AUTH
# ---------------------------------------------------------------------------

@app.post("/api/auth/login")
async def login(req: LoginRequest, response: Response):
    if not verify_password(req.password):
        # Tiny delay to slow down brute force
        await asyncio.sleep(0.5)
        raise HTTPException(status_code=401, detail="סיסמה שגויה")
    issue_session(response)
    return {"ok": True}


@app.post("/api/auth/logout")
def logout(response: Response):
    clear_session(response)
    return {"ok": True}


@app.get("/api/auth/status")
def auth_status(recipe_session: Optional[str] = Cookie(default=None)):
    return {"authenticated": is_authenticated(recipe_session)}


# ---------------------------------------------------------------------------
# CATEGORIES + PROVIDERS
# ---------------------------------------------------------------------------

@app.get("/api/categories")
def get_categories(_: bool = Depends(require_auth)):
    return [c.model_dump() for c in CATEGORIES]


@app.get("/api/providers", response_model=ProvidersResponse)
def get_providers(_: bool = Depends(require_auth)):
    return ProvidersResponse(providers=list_providers())


# ---------------------------------------------------------------------------
# RECIPES — CRUD
# ---------------------------------------------------------------------------

@app.get("/api/recipes")
def list_recipes(db: Session = Depends(get_db), _: bool = Depends(require_auth)):
    rows = db.query(Recipe).order_by(Recipe.date.desc(), Recipe.created_at.desc()).all()
    return [r.to_dict() for r in rows]


@app.get("/api/recipes/{recipe_id}")
def get_recipe(recipe_id: str, db: Session = Depends(get_db), _: bool = Depends(require_auth)):
    r = db.query(Recipe).filter(Recipe.id == recipe_id).first()
    if not r:
        raise HTTPException(404, "מתכון לא נמצא")
    return r.to_dict()


@app.post("/api/recipes")
def create_recipe(body: RecipeIn, db: Session = Depends(get_db), _: bool = Depends(require_auth)):
    rid = f"r_{uuid.uuid4().hex[:12]}"
    pdf_name = None
    screen_name = None

    if body.capture_session_id:
        pdf_name, screen_name = promote_session_to_recipe(body.capture_session_id, rid)

    from datetime import datetime
    r = Recipe(
        id=rid,
        title=body.title.strip(),
        category=body.category or "other",
        url=body.url,
        ingredients=body.ingredients or "",
        instructions=body.instructions or "",
        notes=body.notes or "",
        added_by=body.added_by or "",
        date=body.date or datetime.utcnow().strftime("%Y-%m-%d"),
        pdf_filename=pdf_name,
        screenshot_filename=screen_name,
        captured_at=datetime.utcnow() if (pdf_name or screen_name) else None,
        capture_source_url=body.url if (pdf_name or screen_name) else None,
    )
    db.add(r)
    db.commit()
    db.refresh(r)
    return r.to_dict()


@app.put("/api/recipes/{recipe_id}")
def update_recipe(recipe_id: str, body: RecipeUpdate, db: Session = Depends(get_db),
                  _: bool = Depends(require_auth)):
    r = db.query(Recipe).filter(Recipe.id == recipe_id).first()
    if not r:
        raise HTTPException(404, "מתכון לא נמצא")
    for field in ["title", "category", "url", "ingredients", "instructions",
                  "notes", "added_by", "date"]:
        v = getattr(body, field, None)
        if v is not None:
            setattr(r, field, v)
    if body.clear_image and r.image_filename:
        path = os.path.join(settings.data_dir, "images", r.image_filename)
        if os.path.isfile(path):
            try:
                os.remove(path)
            except OSError:
                pass
        r.image_filename = None
    db.commit()
    db.refresh(r)
    return r.to_dict()


@app.delete("/api/recipes/{recipe_id}")
def delete_recipe(recipe_id: str, db: Session = Depends(get_db),
                  _: bool = Depends(require_auth)):
    r = db.query(Recipe).filter(Recipe.id == recipe_id).first()
    if not r:
        raise HTTPException(404, "מתכון לא נמצא")

    # delete user-uploaded image
    if r.image_filename:
        path = os.path.join(settings.data_dir, "images", r.image_filename)
        try:
            os.remove(path)
        except OSError:
            pass

    # delete capture dir
    cap_dir = recipe_capture_dir(recipe_id)
    if os.path.isdir(cap_dir):
        shutil.rmtree(cap_dir, ignore_errors=True)

    db.delete(r)
    db.commit()
    return {"ok": True}


# ---------------------------------------------------------------------------
# IMAGE upload / fetch
# ---------------------------------------------------------------------------

ALLOWED_IMAGE_TYPES = {"image/jpeg", "image/png", "image/webp", "image/gif"}


@app.post("/api/recipes/{recipe_id}/image")
async def upload_image(
    recipe_id: str,
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    _: bool = Depends(require_auth),
):
    r = db.query(Recipe).filter(Recipe.id == recipe_id).first()
    if not r:
        raise HTTPException(404, "מתכון לא נמצא")
    if file.content_type not in ALLOWED_IMAGE_TYPES:
        raise HTTPException(415, "סוג תמונה לא נתמך")

    raw = await file.read()
    if len(raw) > settings.max_image_size_mb * 1024 * 1024:
        raise HTTPException(413, f"התמונה גדולה מ-{settings.max_image_size_mb}MB")

    # Compress with Pillow to a sensible max dimension
    from io import BytesIO
    from PIL import Image
    img = Image.open(BytesIO(raw))
    img.thumbnail((1600, 1600))
    if img.mode in ("RGBA", "P"):
        img = img.convert("RGB")

    # Delete old image if any
    if r.image_filename:
        old = os.path.join(settings.data_dir, "images", r.image_filename)
        try:
            os.remove(old)
        except OSError:
            pass

    fname = f"{recipe_id}_{uuid.uuid4().hex[:6]}.jpg"
    out_path = os.path.join(settings.data_dir, "images", fname)
    img.save(out_path, format="JPEG", quality=82, optimize=True)

    r.image_filename = fname
    db.commit()
    db.refresh(r)
    return r.to_dict()



# ---------------------------------------------------------------------------
# STEP IMAGES — multiple photos per recipe
# ---------------------------------------------------------------------------

@app.post("/api/recipes/{recipe_id}/step-images")
async def add_step_image(
    recipe_id: str,
    file: UploadFile = File(...),
    caption: str = Form(default=""),
    db: Session = Depends(get_db),
    _: bool = Depends(require_auth),
):
    import json as _json
    r = db.query(Recipe).filter(Recipe.id == recipe_id).first()
    if not r:
        raise HTTPException(404, "מתכון לא נמצא")
    if file.content_type not in ALLOWED_IMAGE_TYPES:
        raise HTTPException(415, "סוג תמונה לא נתמך")

    raw = await file.read()
    if len(raw) > settings.max_image_size_mb * 1024 * 1024:
        raise HTTPException(413, f"התמונה גדולה מ-{settings.max_image_size_mb}MB")

    from io import BytesIO
    from PIL import Image
    img = Image.open(BytesIO(raw))
    img.thumbnail((1600, 1600))
    if img.mode in ("RGBA", "P"):
        img = img.convert("RGB")
    fname = f"{recipe_id}_step_{uuid.uuid4().hex[:6]}.jpg"
    img.save(os.path.join(settings.data_dir, "images", fname), format="JPEG", quality=82)

    steps = _json.loads(r.step_images or "[]")
    steps.append({"filename": fname, "caption": caption})
    r.step_images = _json.dumps(steps, ensure_ascii=False)
    db.commit()
    db.refresh(r)
    return r.to_dict()


@app.delete("/api/recipes/{recipe_id}/step-images/{index}")
def delete_step_image(
    recipe_id: str, index: int,
    db: Session = Depends(get_db),
    _: bool = Depends(require_auth),
):
    import json as _json
    r = db.query(Recipe).filter(Recipe.id == recipe_id).first()
    if not r:
        raise HTTPException(404, "מתכון לא נמצא")
    steps = _json.loads(r.step_images or "[]")
    if index < 0 or index >= len(steps):
        raise HTTPException(404, "תמונה לא נמצאה")
    removed = steps.pop(index)
    # Delete file
    path = os.path.join(settings.data_dir, "images", removed["filename"])
    try:
        os.remove(path)
    except OSError:
        pass
    r.step_images = _json.dumps(steps, ensure_ascii=False)
    db.commit()
    db.refresh(r)
    return r.to_dict()


@app.get("/api/images/{filename}")  # already exists for main image; step images share same dir
def serve_image(filename: str, _: bool = Depends(require_auth)):
    if "/" in filename or ".." in filename:
        raise HTTPException(400, "bad path")
    path = os.path.join(settings.data_dir, "images", filename)
    if not os.path.isfile(path):
        raise HTTPException(404, "תמונה לא נמצאה")
    return FileResponse(path)


# ---------------------------------------------------------------------------
# CAPTURED PDF / SCREENSHOT
# ---------------------------------------------------------------------------

def _capture_path(recipe: Recipe, kind: str) -> str:
    name = recipe.pdf_filename if kind == "pdf" else recipe.screenshot_filename
    if not name:
        raise HTTPException(404, f"אין {'PDF' if kind=='pdf' else 'צילום מסך'} למתכון זה")
    path = os.path.join(recipe_capture_dir(recipe.id), name)
    if not os.path.isfile(path):
        raise HTTPException(404, "הקובץ לא נמצא")
    return path


@app.get("/api/recipes/{recipe_id}/pdf")
def get_recipe_pdf(recipe_id: str, db: Session = Depends(get_db),
                   _: bool = Depends(require_auth)):
    r = db.query(Recipe).filter(Recipe.id == recipe_id).first()
    if not r:
        raise HTTPException(404, "מתכון לא נמצא")
    return FileResponse(_capture_path(r, "pdf"), media_type="application/pdf")


@app.get("/api/recipes/{recipe_id}/screenshot")
def get_recipe_screenshot(recipe_id: str, db: Session = Depends(get_db),
                          _: bool = Depends(require_auth)):
    r = db.query(Recipe).filter(Recipe.id == recipe_id).first()
    if not r:
        raise HTTPException(404, "מתכון לא נמצא")
    return FileResponse(_capture_path(r, "screenshot"), media_type="image/jpeg")


@app.post("/api/recipes/{recipe_id}/recapture")
async def recapture(recipe_id: str, db: Session = Depends(get_db),
                    _: bool = Depends(require_auth)):
    import logging
    logger = logging.getLogger("recapture")
    r = db.query(Recipe).filter(Recipe.id == recipe_id).first()
    if not r:
        raise HTTPException(404, "מתכון לא נמצא")
    if not r.url:
        raise HTTPException(400, "אין URL למתכון - לא ניתן לבצע capture")

    # Strategy 1: direct Playwright navigation (works for most sites)
    result = await capture_url(r.url)

    # Strategy 2: if direct navigation failed or produced thin content,
    # try fetching HTML first then rendering offline — avoids CAPTCHA
    if result.error or not result.screenshot_bytes:
        logger.info("recapture: direct failed (%s), trying HTML-first approach", result.error)
        result2 = await capture_from_fetched_html(r.url)
        if not result2.error and result2.screenshot_bytes:
            result = result2
            logger.info("recapture: HTML-first approach succeeded")
        else:
            logger.warning("recapture: both methods failed. direct=%s html=%s",
                           result.error, result2.error)
            raise HTTPException(502, f"שתי שיטות capture נכשלו:\n1. {result.error}\n2. {result2.error}")

    d = recipe_capture_dir(recipe_id)
    os.makedirs(d, exist_ok=True)
    if result.pdf_bytes:
        with open(os.path.join(d, "page.pdf"), "wb") as f:
            f.write(result.pdf_bytes)
        r.pdf_filename = "page.pdf"
    if result.screenshot_bytes:
        with open(os.path.join(d, "page.jpg"), "wb") as f:
            f.write(result.screenshot_bytes)
        r.screenshot_filename = "page.jpg"

    from datetime import datetime
    r.captured_at = datetime.utcnow()
    r.capture_source_url = r.url
    db.commit()
    db.refresh(r)
    return r.to_dict()


# ---------------------------------------------------------------------------
# DELETE CAPTURE — clear bad PDF/screenshot (CAPTCHA, login walls, etc.)
# ---------------------------------------------------------------------------

@app.delete("/api/recipes/{recipe_id}/capture")
def delete_capture(recipe_id: str, db: Session = Depends(get_db),
                   _: bool = Depends(require_auth)):
    """Delete stored PDF and screenshot for a recipe (e.g. when they captured a CAPTCHA page).
    The recipe itself and its structured fields are preserved."""
    r = db.query(Recipe).filter(Recipe.id == recipe_id).first()
    if not r:
        raise HTTPException(404, "מתכון לא נמצא")
    cap_dir = recipe_capture_dir(recipe_id)
    if os.path.isdir(cap_dir):
        shutil.rmtree(cap_dir, ignore_errors=True)
    r.pdf_filename = None
    r.screenshot_filename = None
    r.captured_at = None
    r.capture_source_url = None
    db.commit()
    db.refresh(r)
    return r.to_dict()


# ---------------------------------------------------------------------------
# BATCH EXTRACT — fill in empty recipes using their saved screenshots
# ---------------------------------------------------------------------------

@app.post("/api/recipes/batch-extract")
async def batch_extract(
    body: dict = {},
    db: Session = Depends(get_db),
    _: bool = Depends(require_auth),
):
    """For every recipe that has a screenshot but empty structured fields,
    run LLM vision extraction and update the recipe in place.
    Returns immediately with a count; processing happens synchronously (one by one)."""
    import logging
    logger = logging.getLogger("batch_extract")

    providers = body.get("providers", ["anthropic", "openai", "xai", "gemini", "groq", "openrouter"])
    mode = body.get("mode", "fallback")
    use_fallback = mode == "fallback"

    # Find empty recipes that have a screenshot to work from
    empty = db.query(Recipe).filter(
        (Recipe.ingredients == "") | Recipe.ingredients.is_(None),
        (Recipe.instructions == "") | Recipe.instructions.is_(None),
        Recipe.screenshot_filename.isnot(None),
    ).all()

    results = {"total": len(empty), "updated": 0, "failed": 0, "details": []}

    for recipe in empty:
        screenshot_path = os.path.join(recipe_capture_dir(recipe.id), recipe.screenshot_filename)
        if not os.path.isfile(screenshot_path):
            results["failed"] += 1
            results["details"].append({"id": recipe.id, "title": recipe.title, "status": "screenshot missing"})
            continue

        try:
            with open(screenshot_path, "rb") as f:
                img_bytes = f.read()

            if use_fallback:
                extraction_results = await extract_with_fallback_vision([img_bytes], providers)
            else:
                extraction_results = await extract_with_providers_vision([img_bytes], recipe.title, providers)
            extraction_results = await _apply_cleanup(extraction_results, providers)
            success = next((r for r in extraction_results if r.success), None)

            if success and success.data:
                d = success.data
                # Only update non-empty fields from extraction
                if d.title and not recipe.title:
                    recipe.title = d.title
                if d.category and d.category != "other":
                    recipe.category = d.category
                if d.ingredients:
                    recipe.ingredients = d.ingredients
                if d.instructions:
                    recipe.instructions = d.instructions
                if d.notes and not recipe.notes:
                    recipe.notes = d.notes
                db.commit()
                results["updated"] += 1
                results["details"].append({"id": recipe.id, "title": recipe.title, "status": "updated", "provider": success.provider})
                logger.info("batch_extract: updated %r via %s", recipe.title, success.provider)
            else:
                errors = "; ".join(r.error or "" for r in extraction_results if not r.success)
                results["failed"] += 1
                results["details"].append({"id": recipe.id, "title": recipe.title, "status": "extraction failed", "error": errors})
                logger.warning("batch_extract: failed for %r: %s", recipe.title, errors)

        except Exception as e:
            db.rollback()
            results["failed"] += 1
            results["details"].append({"id": recipe.id, "title": recipe.title, "status": "error", "error": str(e)})
            logger.exception("batch_extract: exception for %r", recipe.title)

        # Small delay between recipes to avoid hammering LLM rate limits
        await asyncio.sleep(1)

    return results


# ---------------------------------------------------------------------------
# EXTRACTION (the AI part — used only at add time)
# ---------------------------------------------------------------------------

async def _apply_cleanup(results: list, providers: list[str]) -> list:
    """If settings.hebrew_cleanup, run cleanup pass on the first successful result."""
    if not settings.hebrew_cleanup:
        return results
    for i, r in enumerate(results):
        if r.success and r.data:
            import logging
            logging.getLogger("llm").info("Running Hebrew cleanup pass via %s", providers)
            cleaned = await cleanup_hebrew(r.data, providers)
            from schemas import ProviderResult
            results[i] = ProviderResult(
                provider=r.provider, success=True,
                data=cleaned, elapsed_ms=r.elapsed_ms,
            )
            break
    return results
async def extract(req: ExtractRequest, _: bool = Depends(require_auth)):
    # Always capture if requested. Even on partial failure we try to return what we have.
    capture_info: Optional[CaptureInfo] = None
    page_text = ""
    page_title = ""

    if req.capture:
        result = await capture_url(req.url)
        # Fallback: if direct navigation failed or got CAPTCHA, try HTML-first
        if result.error or not result.screenshot_bytes:
            result2 = await capture_from_fetched_html(req.url)
            if not result2.error and result2.screenshot_bytes:
                result = result2
        if result.error and not result.screenshot_bytes:
            raise HTTPException(502, f"שגיאת capture: {result.error}")
        # Clean HTML for LLM input
        page_title, page_text = clean_html_to_text(result.html)
        # Save capture under a temporary session
        session_id = f"s_{uuid.uuid4().hex}"
        save_session_capture(session_id, result)
        capture_info = CaptureInfo(
            session_id=session_id,
            has_pdf=bool(result.pdf_bytes),
            has_screenshot=bool(result.screenshot_bytes),
            screenshot_url=f"/api/extract/session/{session_id}/screenshot" if result.screenshot_bytes else None,
            pdf_url=f"/api/extract/session/{session_id}/pdf" if result.pdf_bytes else None,
        )
    else:
        # No capture - just fetch HTML via httpx for the text
        import httpx
        async with httpx.AsyncClient(timeout=settings.fetch_timeout_seconds,
                                     follow_redirects=True) as client:
            resp = await client.get(req.url, headers={
                "User-Agent": "Mozilla/5.0 RecipeBook/1.0",
                "Accept-Language": "he-IL,he;q=0.9,en;q=0.8",
            })
            resp.raise_for_status()
            page_title, page_text = clean_html_to_text(resp.text)

    if not page_text:
        raise HTTPException(422, "לא ניתן לחלץ תוכן מהדף")

    # Run all selected providers in parallel or sequential fallback
    use_fallback = req.mode == "fallback"
    if use_fallback:
        results = await extract_with_fallback(page_text, page_title, req.url, req.providers)
    else:
        results = await extract_with_providers(page_text, page_title, req.url, req.providers)

    results = await _apply_cleanup(results, req.providers)

    return ExtractResponse(
        url=req.url,
        source_domain=domain_of(req.url),
        page_title=page_title,
        capture=capture_info,
        results=results,
    )


@app.post("/api/extract/file", response_model=ExtractResponse)
async def extract_from_file(
    file: UploadFile = File(...),
    providers: str = Form(default='["gemini","groq","ollama","openai","anthropic"]'),
    mode: str = Form(default="fallback"),
    _: bool = Depends(require_auth),
):
    """Extract a recipe from an uploaded PDF or image using LLM (text or vision)."""
    import json as _json
    import logging
    logger = logging.getLogger("extract_file")

    try:
        providers_list = _json.loads(providers)
    except Exception:
        providers_list = ["anthropic", "openai"]

    raw = await file.read()
    filename = file.filename or "upload"
    content_type = file.content_type or ""
    logger.info("extract_from_file: filename=%s size=%d providers=%s", filename, len(raw), providers_list)

    image_pages: list[bytes] = []
    text_content: str = ""
    session_pdf: bytes | None = None
    session_img: bytes | None = None

    is_pdf = content_type == "application/pdf" or filename.lower().endswith(".pdf")

    if is_pdf:
        try:
            import fitz  # pymupdf
            doc = fitz.open(stream=raw, filetype="pdf")
            session_pdf = raw

            for page_num, page in enumerate(doc):
                # Always extract text
                text_content += page.get_text()
                # Render page to JPEG at 3× scale for sharp vision input
                mat = fitz.Matrix(3.0, 3.0)
                pix = page.get_pixmap(matrix=mat, alpha=False)
                jpg = pix.tobytes("jpeg", jpg_quality=90)
                image_pages.append(jpg)
                if not session_img:
                    session_img = jpg
                if page_num >= 3:  # max 4 pages
                    break
            doc.close()
            logger.info("PDF processed: %d pages, text_len=%d, img_pages=%d",
                        len(image_pages), len(text_content.strip()), len(image_pages))
        except Exception as e:
            logger.exception("PDF processing error")
            raise HTTPException(422, f"שגיאה בעיבוד PDF: {e}")

    elif content_type.startswith("image/") or any(
        filename.lower().endswith(ext) for ext in (".jpg", ".jpeg", ".png", ".webp", ".gif")
    ):
        from io import BytesIO
        from PIL import Image as PilImage
        try:
            img = PilImage.open(BytesIO(raw))
            img.thumbnail((2400, 2400))
            if img.mode in ("RGBA", "P"):
                img = img.convert("RGB")
            buf = BytesIO()
            img.save(buf, format="JPEG", quality=90, optimize=True)
            jpg_bytes = buf.getvalue()
            image_pages = [jpg_bytes]
            session_img = jpg_bytes
            logger.info("Image processed: size=%dx%d", img.width, img.height)
        except Exception as e:
            logger.exception("Image processing error")
            raise HTTPException(422, f"שגיאה בעיבוד תמונה: {e}")
    else:
        raise HTTPException(415, "סוג קובץ לא נתמך. יש להעלות PDF או תמונה (JPEG, PNG, WEBP).")

    if not image_pages and not text_content:
        raise HTTPException(422, "לא ניתן לעבד את הקובץ — נסי קובץ אחר")

    # --- Save to temp session ------------------------------------------------
    session_id = f"s_{uuid.uuid4().hex}"
    sess_dir = os.path.join(settings.data_dir, "sessions", session_id)
    os.makedirs(sess_dir, exist_ok=True)
    if session_pdf:
        with open(os.path.join(sess_dir, "page.pdf"), "wb") as f:
            f.write(session_pdf)
    if session_img:
        with open(os.path.join(sess_dir, "page.jpg"), "wb") as f:
            f.write(session_img)

    capture_info = CaptureInfo(
        session_id=session_id,
        has_pdf=bool(session_pdf),
        has_screenshot=bool(session_img),
        screenshot_url=f"/api/extract/session/{session_id}/screenshot" if session_img else None,
        pdf_url=f"/api/extract/session/{session_id}/pdf" if session_pdf else None,
    )

    # --- Choose extraction strategy ------------------------------------------
    # If the PDF has real embedded text (>300 chars), use text LLM — more
    # reliable than vision for digital PDFs. For scanned/image-only PDFs,
    # fall back to vision.
    clean_text = text_content.strip()
    use_vision = not clean_text or len(clean_text) < 300

    use_fallback = mode == "fallback"
    logger.info("Extraction strategy: %s (text_len=%d, mode=%s)",
                "vision" if use_vision else "text", len(clean_text), mode)

    if use_vision:
        if use_fallback:
            results = await extract_with_fallback_vision(image_pages, providers_list)
        else:
            results = await extract_with_providers_vision(image_pages, filename, providers_list)
    else:
        if use_fallback:
            results = await extract_with_fallback(clean_text, filename, "", providers_list)
        else:
            results = await extract_with_providers(clean_text, filename, "", providers_list)

    results = await _apply_cleanup(results, providers_list)

    # Log results for debugging
    for r in results:
        if r.success:
            logger.info("Provider %s succeeded: title=%r", r.provider, r.data.title if r.data else None)
        else:
            logger.warning("Provider %s failed: %s", r.provider, r.error)

    return ExtractResponse(
        url=None,
        source_domain="קובץ מקומי",
        page_title=filename,
        capture=capture_info,
        results=results,
    )


def _session_file(session_id: str, name: str) -> str:
    if not session_id.startswith("s_") or "/" in session_id or ".." in session_id:
        raise HTTPException(400, "bad session id")
    path = os.path.join(settings.data_dir, "sessions", session_id, name)
    if not os.path.isfile(path):
        raise HTTPException(404, "not found")
    return path


@app.get("/api/extract/session/{session_id}/pdf")
def session_pdf(session_id: str, _: bool = Depends(require_auth)):
    return FileResponse(_session_file(session_id, "page.pdf"), media_type="application/pdf")


@app.get("/api/extract/session/{session_id}/screenshot")
def session_screenshot(session_id: str, _: bool = Depends(require_auth)):
    return FileResponse(_session_file(session_id, "page.jpg"), media_type="image/jpeg")


# ---------------------------------------------------------------------------
# SHARING (no auth)
# ---------------------------------------------------------------------------

@app.post("/api/recipes/{recipe_id}/share", response_model=ShareResponse)
def create_share(recipe_id: str, db: Session = Depends(get_db),
                 _: bool = Depends(require_auth)):
    r = db.query(Recipe).filter(Recipe.id == recipe_id).first()
    if not r:
        raise HTTPException(404, "מתכון לא נמצא")
    if not r.share_token:
        r.share_token = new_share_token()
        db.commit()
        db.refresh(r)
    base = settings.share_base_url.rstrip("/")
    return ShareResponse(
        share_token=r.share_token,
        share_url=f"{base}/share/{r.share_token}",
    )


@app.delete("/api/recipes/{recipe_id}/share")
def revoke_share(recipe_id: str, db: Session = Depends(get_db),
                 _: bool = Depends(require_auth)):
    r = db.query(Recipe).filter(Recipe.id == recipe_id).first()
    if not r:
        raise HTTPException(404, "מתכון לא נמצא")
    r.share_token = None
    db.commit()
    return {"ok": True}


@app.get("/api/share/{token}")
def share_get(token: str, db: Session = Depends(get_db)):
    r = db.query(Recipe).filter(Recipe.share_token == token).first()
    if not r:
        raise HTTPException(404, "קישור לא תקף")
    # Return a slimmed dict (don't leak internal fields)
    d = r.to_dict()
    # Adjust URLs to point at the public share endpoints
    if r.image_filename:
        d["image_url"] = f"/api/share/{token}/image"
    if r.pdf_filename:
        d["pdf_url"] = f"/api/share/{token}/pdf"
    if r.screenshot_filename:
        d["screenshot_url"] = f"/api/share/{token}/screenshot"
    # Don't leak internal fields:
    for k in ("share_token", "captured_at", "created_at", "updated_at", "image_filename"):
        d.pop(k, None)
    return d


@app.get("/api/share/{token}/pdf")
def share_pdf(token: str, db: Session = Depends(get_db)):
    r = db.query(Recipe).filter(Recipe.share_token == token).first()
    if not r:
        raise HTTPException(404, "קישור לא תקף")
    return FileResponse(_capture_path(r, "pdf"), media_type="application/pdf")


@app.get("/api/share/{token}/screenshot")
def share_screenshot(token: str, db: Session = Depends(get_db)):
    r = db.query(Recipe).filter(Recipe.share_token == token).first()
    if not r:
        raise HTTPException(404, "קישור לא תקף")
    return FileResponse(_capture_path(r, "screenshot"), media_type="image/jpeg")


@app.get("/api/share/{token}/image")
def share_image(token: str, db: Session = Depends(get_db)):
    r = db.query(Recipe).filter(Recipe.share_token == token).first()
    if not r or not r.image_filename:
        raise HTTPException(404, "לא נמצא")
    path = os.path.join(settings.data_dir, "images", r.image_filename)
    if not os.path.isfile(path):
        raise HTTPException(404, "לא נמצא")
    return FileResponse(path)


# ---------------------------------------------------------------------------
# HEALTH + SPA fallback
# ---------------------------------------------------------------------------

@app.get("/api/health")
def health():
    return {"ok": True}


# Static files are served by the dedicated frontend nginx container.
# This backend is a pure API server — no static file handling here.
