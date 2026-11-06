"""Fetch and convert the documents linked from kgromero.com for get_document.

Only URLs listed on the site (list_documents) can be fetched, so the tool can't be
used to reach arbitrary hosts.
"""

from __future__ import annotations

import base64
import io
import re
from collections import OrderedDict
from dataclasses import dataclass
from urllib.parse import urlparse

import httpx
import pypdfium2 as pdfium
from mcp_types import ImageContent, ResourceLink, TextContent
from PIL import Image as PILImage

from . import data

MAX_DOWNLOAD = 25 * 1024 * 1024
MAX_TEXT_CHARS = 40_000
MAX_IMAGE_PAGES = 5
IMAGE_LONG_EDGE = 1400  # px; Claude downsizes anything over ~1568 anyway
SPARSE_PAGE_CHARS = 200  # pages with less text than this are probably slides/diagrams
VIDEO_EXTS = {".webm", ".mp4", ".mov"}
IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".gif", ".webp"}
MIME_TYPES = {
    ".pdf": "application/pdf",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".webp": "image/webp",
    ".webm": "video/webm",
    ".mp4": "video/mp4",
    ".mov": "video/quicktime",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".md": "text/markdown",
    ".vcf": "text/vcard",
    ".json": "application/json",
}

_bytes_cache: OrderedDict[str, bytes] = OrderedDict()
_CACHE_BUDGET = 40 * 1024 * 1024


@dataclass
class Doc:
    group: str
    name: str
    url: str

    @property
    def ext(self) -> str:
        path = urlparse(self.url).path.lower()
        return path[path.rfind("."):] if "." in path.rsplit("/", 1)[-1] else ""

    @property
    def on_site(self) -> bool:
        return urlparse(self.url).netloc == urlparse(data.SITE_URL).netloc


def resume_group() -> dict:
    s = data.SITE_URL
    return {
        "group": "Resume & contact",
        "items": [
            {"name": "Resume (PDF)", "url": f"{s}/Kyle_Romero-Resume.pdf"},
            {"name": "Resume (Word)", "url": f"{s}/Kyle_Romero-Resume.docx"},
            {"name": "Resume (Markdown)", "url": f"{s}/kgromero.md"},
            {"name": "Resume (JSON Resume)", "url": f"{s}/api/resume"},
            {"name": "Cover letter (PDF)", "url": f"{s}/Kyle_Romero-Coverletter.pdf"},
            {"name": "Contact card (vCard)", "url": f"{s}/kgromero.vcf"},
            {"name": "API docs (OpenAPI)", "url": f"{s}/api/openapi.json"},
        ],
    }


async def all_documents() -> list[Doc]:
    groups = [resume_group(), *(await data.get_site()).get("documents", [])]
    return [Doc(g["group"], i["name"], i["url"]) for g in groups for i in g["items"]]


async def find(query: str) -> Doc:
    """Resolve a name, partial name, filename, or URL to exactly one listed document."""
    docs = await all_documents()
    q = query.strip()
    ql = q.lower()
    by_url = [d for d in docs if d.url == q or d.url.rstrip("/") == q.rstrip("/")]
    if by_url:
        return by_url[0]
    for match in (
        lambda d: d.name.lower() == ql,
        lambda d: urlparse(d.url).path.rsplit("/", 1)[-1].lower() == ql,
        lambda d: ql in d.name.lower(),
        lambda d: ql in f"{d.group} {d.name}".lower(),
        lambda d: ql in urlparse(d.url).path.lower(),
    ):
        hits = [d for d in docs if match(d)]
        if len(hits) == 1:
            return hits[0]
        if len(hits) > 1:
            names = "; ".join(f"{d.name} ({d.group})" for d in hits[:15])
            raise ValueError(f"{query!r} matches several documents, be more specific: {names}")
    if q.startswith(("http://", "https://")):
        raise ValueError("Only documents listed by list_documents can be fetched.")
    raise ValueError(f"No document matches {query!r}. Use list_documents to see what's available.")


async def fetch_bytes(url: str) -> bytes:
    if url in _bytes_cache:
        _bytes_cache.move_to_end(url)
        return _bytes_cache[url]
    async with httpx.AsyncClient(timeout=30, follow_redirects=True) as client:
        async with client.stream("GET", url, headers={"User-Agent": "kgromero-mcp"}) as resp:
            resp.raise_for_status()
            buf = bytearray()
            async for chunk in resp.aiter_bytes():
                buf += chunk
                if len(buf) > MAX_DOWNLOAD:
                    raise ValueError(f"Document is larger than {MAX_DOWNLOAD // 2**20} MB; open it at {url}")
    body = bytes(buf)
    _bytes_cache[url] = body
    while sum(map(len, _bytes_cache.values())) > _CACHE_BUDGET and len(_bytes_cache) > 1:
        _bytes_cache.popitem(last=False)
    return body


def parse_pages(spec: str | None, count: int) -> list[int]:
    """'3', '1-5', '2,4,7-9' -> zero-based page indexes. None means all pages."""
    if not spec:
        return list(range(count))
    pages: list[int] = []
    for part in spec.replace(" ", "").split(","):
        m = re.fullmatch(r"(\d+)(?:-(\d+))?", part)
        if not m:
            raise ValueError(f"Invalid pages {spec!r}; use e.g. '3', '1-5', or '2,4,7-9'")
        a, b = int(m.group(1)), int(m.group(2) or m.group(1))
        if a < 1 or b < a or b > count:
            raise ValueError(f"Pages {part!r} out of range; this document has {count} pages")
        pages += [p for p in range(a - 1, b) if p not in pages]
    return pages


def to_image_content(img: PILImage.Image) -> ImageContent:
    """Downscale and encode for the model: JPEG for photos/renders, keeping size small."""
    img = img.convert("RGB") if img.mode not in ("RGB", "L") else img
    img.thumbnail((IMAGE_LONG_EDGE, IMAGE_LONG_EDGE))
    out = io.BytesIO()
    img.save(out, "JPEG", quality=82, optimize=True)
    return ImageContent(type="image", data=base64.b64encode(out.getvalue()).decode(), mime_type="image/jpeg")


def flatten(img: PILImage.Image) -> PILImage.Image:
    """Put transparent images on white so diagrams don't turn black in JPEG."""
    if img.mode in ("RGBA", "LA", "P"):
        img = img.convert("RGBA")
        bg = PILImage.new("RGB", img.size, "white")
        bg.paste(img, mask=img.split()[-1])
        return bg
    return img


def pdf_content(doc: Doc, body: bytes, pages: str | None, as_images: bool) -> list[TextContent | ImageContent]:
    pdf = pdfium.PdfDocument(body)
    try:
        count = len(pdf)
        idx = parse_pages(pages, count)
        header = f"{doc.name} ({doc.group}), PDF, {count} pages. Source: {doc.url}\n"

        if as_images:
            if len(idx) > MAX_IMAGE_PAGES:
                raise ValueError(
                    f"At most {MAX_IMAGE_PAGES} pages as images per call; pass pages, e.g. '1-{MAX_IMAGE_PAGES}'"
                )
            out: list[TextContent | ImageContent] = [TextContent(type="text", text=header)]
            for i in idx:
                page = pdf[i]
                w, h = page.get_size()
                img = page.render(scale=IMAGE_LONG_EDGE / max(w, h)).to_pil()
                page.close()
                out += [TextContent(type="text", text=f"Page {i + 1}:"), to_image_content(img)]
            return out

        parts, sparse, total, cut_at = [], [], 0, None
        for i in idx:
            page = pdf[i]
            tp = page.get_textpage()
            text = tp.get_text_bounded().replace("\r\n", "\n").replace("\r", "\n").strip()
            tp.close()
            page.close()
            if len(text) < SPARSE_PAGE_CHARS:
                sparse.append(i + 1)
            if total + len(text) > MAX_TEXT_CHARS:
                cut_at = i + 1
                break
            parts.append(f"--- Page {i + 1} ---\n{text}")
            total += len(text)

        notes = []
        if cut_at:
            notes.append(f"Text stopped before page {cut_at} (length limit); pass pages='{cut_at}-{idx[-1] + 1}' for the rest.")
        if sparse:
            shown = ",".join(map(str, sparse[:20])) + ("..." if len(sparse) > 20 else "")
            notes.append(
                f"Pages with little text, likely slides, photos, or diagrams: {shown}. "
                "Call again with as_images=true and pages=... (up to 5) to see them."
            )
        text = header + ("\n".join(notes) + "\n" if notes else "") + "\n" + "\n\n".join(parts)
        return [TextContent(type="text", text=text)]
    finally:
        pdf.close()


async def get(query: str, pages: str | None, as_images: bool) -> list[TextContent | ImageContent]:
    doc = await find(query)
    if not doc.on_site:
        return [TextContent(type="text", text=f"{doc.name} is an external page, not a file on kgromero.com. Open it at {doc.url}")]
    blocks, size = await _content(doc, pages, as_images)
    # Also link the original file, so clients that can open or download files get the real thing.
    return [*blocks, file_link(doc, size)]


def file_link(doc: Doc, size: int | None = None) -> ResourceLink:
    return ResourceLink(
        type="resource_link",
        uri=doc.url,
        name=doc.url.rsplit("/", 1)[-1] or doc.name,
        title=doc.name,
        description=f"Original file ({doc.group})",
        mime_type=MIME_TYPES.get(doc.ext, "application/json" if doc.url.endswith("/api/resume") else None),
        size=size,
    )


async def _content(doc: Doc, pages: str | None, as_images: bool) -> tuple[list[TextContent | ImageContent], int | None]:
    """The model-friendly content for a file on the site, and its size in bytes if fetched."""
    if doc.ext in VIDEO_EXTS:
        return [TextContent(type="text", text=f"{doc.name} is a video, which can't be passed through MCP. Watch it at {doc.url}")], None
    if doc.ext == ".docx":
        # Same content as the PDF/Markdown resume; Markdown is the most useful form for a model.
        md = await data.get_resume_markdown()
        return [TextContent(type="text", text=f"{doc.name}: the Word file has the same content as this Markdown version. Download: {doc.url}\n\n{md}")], None

    body = await fetch_bytes(doc.url)
    if doc.ext == ".pdf":
        return pdf_content(doc, body, pages, as_images), len(body)
    if doc.ext in IMAGE_EXTS:
        img = flatten(PILImage.open(io.BytesIO(body)))
        caption = f"{doc.name} ({doc.group}), image {img.width}x{img.height}. Source: {doc.url}"
        return [TextContent(type="text", text=caption), to_image_content(img)], len(body)
    # JSON, Markdown, vCard, and other text
    text = body.decode("utf-8", errors="replace")
    if len(text) > MAX_TEXT_CHARS:
        text = text[:MAX_TEXT_CHARS] + "\n[truncated]"
    return [TextContent(type="text", text=f"{doc.name} ({doc.group}). Source: {doc.url}\n\n{text}")], len(body)
