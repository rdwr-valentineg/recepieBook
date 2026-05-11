"""Convert raw HTML to clean text content for LLM input."""
import trafilatura
from urllib.parse import urlparse


def domain_of(url: str) -> str:
    try:
        host = urlparse(url).hostname or ""
        return host.removeprefix("www.").removeprefix("mobile.")
    except Exception:
        return ""


def clean_html_to_text(html: str) -> tuple[str, str]:
    """
    Returns (title, clean_text).
    Returns ("", "") if extraction fails.
    """
    if not html:
        return "", ""
    extracted = trafilatura.extract(
        html,
        include_comments=False,
        include_tables=True,
        include_formatting=False,
        favor_recall=True,
    )
    if not extracted:
        return "", ""

    meta = trafilatura.extract_metadata(html)
    title = (meta.title if meta and meta.title else "").strip()

    # Trim very long content
    if len(extracted) > 20000:
        extracted = extracted[:20000] + "\n\n[... content truncated ...]"

    return title, extracted
