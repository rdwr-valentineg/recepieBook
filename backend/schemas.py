"""Pydantic schemas for API request/response."""
from typing import Optional, List
from pydantic import BaseModel, Field


# --- recipes ---------------------------------------------------------------

class RecipeIn(BaseModel):
    title: str
    category: str = "other"
    url: Optional[str] = None
    ingredients: str = ""
    instructions: str = ""
    notes: str = ""
    added_by: str = ""
    date: Optional[str] = None
    # If set, server moves capture files (pdf, screenshot) from this session to
    # the new recipe's directory.
    capture_session_id: Optional[str] = None


class RecipeUpdate(BaseModel):
    title: Optional[str] = None
    category: Optional[str] = None
    url: Optional[str] = None
    ingredients: Optional[str] = None
    instructions: Optional[str] = None
    notes: Optional[str] = None
    added_by: Optional[str] = None
    date: Optional[str] = None
    clear_image: bool = False


# --- auth ------------------------------------------------------------------

class LoginRequest(BaseModel):
    password: str


# --- extraction ------------------------------------------------------------

class ExtractRequest(BaseModel):
    url: str
    providers: List[str] = Field(default_factory=lambda: ["anthropic", "openai"])
    capture: bool = True  # if False, only extract; don't make PDF/screenshot


class ExtractedRecipe(BaseModel):
    title: str = ""
    category: str = "other"
    ingredients: str = ""
    instructions: str = ""
    notes: str = ""


class ProviderResult(BaseModel):
    provider: str
    success: bool
    data: Optional[ExtractedRecipe] = None
    error: Optional[str] = None
    elapsed_ms: int = 0


class CaptureInfo(BaseModel):
    session_id: str
    has_pdf: bool
    has_screenshot: bool
    screenshot_url: Optional[str] = None
    pdf_url: Optional[str] = None


class ExtractResponse(BaseModel):
    url: str
    source_domain: str
    page_title: str = ""
    capture: Optional[CaptureInfo] = None
    results: List[ProviderResult]


# --- providers / share -----------------------------------------------------

class ProviderInfo(BaseModel):
    id: str
    name: str
    model: str
    enabled: bool


class ProvidersResponse(BaseModel):
    providers: List[ProviderInfo]


class ShareResponse(BaseModel):
    share_token: str
    share_url: str   # absolute URL using SHARE_BASE_URL


# --- categories ------------------------------------------------------------

class Category(BaseModel):
    id: str
    label: str
    emoji: str
