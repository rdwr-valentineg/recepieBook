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
    UploadFile, File
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
    COOKIE_NAME
)
from capture import (
    capture_url, save_session_capture, promote_session_to_recipe,
    cleanup_orphan_sessions, recipe_capture_dir, PlaywrightHolder
)
from scraper import clean_html_to_text, domain_of
from llm import extract_with_providers, list_providers
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


@app.get("/api/images/{filename}")
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
    r = db.query(Recipe).filter(Recipe.id == recipe_id).first()
    if not r:
        raise HTTPException(404, "מתכון לא נמצא")
    if not r.url:
        raise HTTPException(400, "אין URL למתכון - לא ניתן לבצע capture")

    result = await capture_url(r.url)
    if result.error:
        raise HTTPException(502, f"capture נכשל: {result.error}")

    # write to recipe dir
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
# EXTRACTION (the AI part — used only at add time)
# ---------------------------------------------------------------------------

@app.post("/api/extract", response_model=ExtractResponse)
async def extract(req: ExtractRequest, _: bool = Depends(require_auth)):
    # Always capture if requested. Even on partial failure we try to return what we have.
    capture_info: Optional[CaptureInfo] = None
    page_text = ""
    page_title = ""

    if req.capture:
        result = await capture_url(req.url)
        if result.error:
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

    # Run all selected providers in parallel
    results = await extract_with_providers(page_text, page_title, req.url, req.providers)

    return ExtractResponse(
        url=req.url,
        source_domain=domain_of(req.url),
        page_title=page_title,
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
