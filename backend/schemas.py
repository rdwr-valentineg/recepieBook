"""Pydantic schemas for API request/response."""

from pydantic import BaseModel, Field

# --- recipes ---------------------------------------------------------------

class RecipeIn(BaseModel):
    title: str
    category: str = "other"
    url: str | None = None
    ingredients: str = ""
    instructions: str = ""
    notes: str = ""
    added_by: str = ""
    date: str | None = None
    # If set, server moves capture files (pdf, screenshot) from this session to
    # the new recipe's directory.
    capture_session_id: str | None = None


class RecipeUpdate(BaseModel):
    title: str | None = None
    category: str | None = None
    url: str | None = None
    ingredients: str | None = None
    instructions: str | None = None
    notes: str | None = None
    added_by: str | None = None
    date: str | None = None
    clear_image: bool = False


# --- auth ------------------------------------------------------------------

class LoginRequest(BaseModel):
    password: str


# --- ingredient review / conversion ----------------------------------------

class IngredientAnalyzeRequest(BaseModel):
    ingredients: str = ""


class IngredientConvertRequest(BaseModel):
    ingredients: str = ""
    # Restrict conversion to these line indices. None = convert everything
    # that can be converted.
    indices: list[int] | None = None


# --- extraction ------------------------------------------------------------

class ExtractRequest(BaseModel):
    url: str
    providers: list[str] = Field(default_factory=lambda: ["anthropic", "openai", "xai", "gemini", "groq", "openrouter", "ollama"])
    mode: str = "fallback"   # "fallback" = sequential, stop at first success | "parallel" = all at once
    capture: bool = True


class ExtractedRecipe(BaseModel):
    title: str = ""
    category: str = "other"
    ingredients: str = ""
    instructions: str = ""
    notes: str = ""


class ProviderResult(BaseModel):
    provider: str
    success: bool
    data: ExtractedRecipe | None = None
    error: str | None = None
    elapsed_ms: int = 0


class CaptureInfo(BaseModel):
    session_id: str
    has_pdf: bool
    has_screenshot: bool
    screenshot_url: str | None = None
    pdf_url: str | None = None


class ExtractResponse(BaseModel):
    url: str | None = None
    source_domain: str
    page_title: str = ""
    capture: CaptureInfo | None = None
    results: list[ProviderResult]


# --- providers / share -----------------------------------------------------

class ProviderInfo(BaseModel):
    id: str
    name: str
    model: str
    enabled: bool


class ProvidersResponse(BaseModel):
    providers: list[ProviderInfo]


class ShareResponse(BaseModel):
    share_token: str
    share_url: str   # absolute URL using SHARE_BASE_URL


# --- categories ------------------------------------------------------------

class Category(BaseModel):
    id: str
    label: str
    emoji: str
