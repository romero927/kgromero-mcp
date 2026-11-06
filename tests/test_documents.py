"""get_document, using generated files instead of the network."""

import io

import pytest
from mcp import Client
from PIL import Image

from kgromero_mcp import data, documents
from kgromero_mcp.server import mcp


def text_pdf(pages: list[str]) -> bytes:
    """A minimal PDF of Helvetica text, wrapped into short lines on each page."""
    objs = ["<< /Type /Catalog /Pages 2 0 R >>", None, "<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>"]
    kids = []
    for text in pages:
        words, lines = text.split(), []
        while words:
            lines.append(" ".join(words[:8]))
            words = words[8:]
        body = " T* ".join(f"({line}) Tj" for line in lines)
        stream = f"BT /F1 10 Tf 12 TL 72 720 Td {body} ET".encode()
        objs.append(f"<< /Length {len(stream)} >>\nstream\n{stream.decode()}\nendstream")
        objs.append(f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
                    f"/Resources << /Font << /F1 3 0 R >> >> /Contents {len(objs)} 0 R >>")
        kids.append(f"{len(objs)} 0 R")
    objs[1] = f"<< /Type /Pages /Kids [{' '.join(kids)}] /Count {len(kids)} >>"
    out, offsets = io.BytesIO(), []
    out.write(b"%PDF-1.4\n")
    for i, o in enumerate(objs, 1):
        offsets.append(out.tell())
        out.write(f"{i} 0 obj\n{o}\nendobj\n".encode())
    xref = out.tell()
    out.write(f"xref\n0 {len(objs) + 1}\n0000000000 65535 f \n".encode())
    out.write("".join(f"{o:010d} 00000 n \n" for o in offsets).encode())
    out.write(f"trailer\n<< /Size {len(objs) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF".encode())
    return out.getvalue()


def image_pdf(n: int) -> bytes:
    """Image-only pages, like scanned docs or slide exports."""
    imgs = [Image.new("RGB", (400, 300), c) for c in ["red", "green", "blue", "gray", "white", "black"][:n]]
    out = io.BytesIO()
    imgs[0].save(out, "PDF", save_all=True, append_images=imgs[1:])
    return out.getvalue()


def png(size=(3000, 1500)) -> bytes:
    out = io.BytesIO()
    Image.new("RGBA", size, (0, 0, 0, 0)).save(out, "PNG")
    return out.getvalue()


FILES = {}


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    monkeypatch.setattr(data, "OFFLINE", True)
    data._cache.clear()
    documents._bytes_cache.clear()

    async def fake_fetch(url):
        name = url.rsplit("/", 1)[-1]
        if name not in FILES:
            raise AssertionError(f"unexpected fetch {url}")
        return FILES[name]

    monkeypatch.setattr(documents, "fetch_bytes", fake_fetch)
    FILES.clear()


async def call(args):
    async with Client(mcp) as client:
        return await client.call_tool("get_document", args)


def test_parse_pages():
    assert documents.parse_pages(None, 3) == [0, 1, 2]
    assert documents.parse_pages("2,4-5", 6) == [1, 3, 4]
    with pytest.raises(ValueError):
        documents.parse_pages("7", 6)
    with pytest.raises(ValueError):
        documents.parse_pages("x", 6)


async def test_pdf_text_by_name():
    FILES["Workflow.pdf"] = text_pdf(["To Do In Progress Ready for Testing " * 8, "Deployed " * 30])
    r = await call({"document": "Team Workflow"})
    assert not r.is_error
    text = r.content[0].text
    assert "PDF, 2 pages" in text and "--- Page 1 ---" in text and "Ready for Testing" in text
    assert "little text" not in text


async def test_pdf_page_range_and_sparse_hint():
    FILES["Lab2FinalPresentation.pdf"] = image_pdf(6)
    r = await call({"document": "Lab 2 PPT", "pages": "2-3"})
    text = r.content[0].text
    assert "6 pages" in text and "--- Page 2 ---" in text and "--- Page 1 ---" not in text
    assert "little text" in text and "as_images=true" in text


async def test_pdf_as_images():
    FILES["Lab2FinalPresentation.pdf"] = image_pdf(6)
    r = await call({"document": "Lab 2 PPT", "pages": "1,3", "as_images": True})
    images = [c for c in r.content if c.type == "image"]
    assert len(images) == 2 and images[0].mime_type == "image/jpeg"
    too_many = await call({"document": "Lab 2 PPT", "as_images": True})
    assert too_many.is_error


async def test_png_is_downscaled_and_flattened():
    FILES["Electron-Architecture.png"] = png()
    r = await call({"document": "Electron Architecture"})
    assert r.content[1].type == "image"
    import base64

    img = Image.open(io.BytesIO(base64.b64decode(r.content[1].data)))
    assert max(img.size) <= documents.IMAGE_LONG_EDGE and img.mode == "RGB"
    assert img.getpixel((10, 10)) == (255, 255, 255)  # transparent -> white, not black


async def test_video_external_and_docx_need_no_fetch():
    video = await call({"document": "RPi LED Display"})
    assert "video" in video.content[0].text
    external = await call({"document": "Press Release"})
    assert "external page" in external.content[0].text
    docx = await call({"document": "Resume (Word)"})
    assert "Kyle Romero" in docx.content[0].text


async def test_text_files():
    FILES["kgromero.vcf"] = b"BEGIN:VCARD\nFN:Kyle Romero\nEND:VCARD"
    r = await call({"document": "vCard"})
    assert "FN:Kyle Romero" in r.content[0].text


async def test_rejects_ambiguous_unknown_and_unlisted_urls():
    for args in ({"document": "Lab"}, {"document": "Nonexistent"}, {"document": "https://example.com/x.pdf"}):
        r = await call(args)
        assert r.is_error, args
    by_url = await call({"document": "https://kgromero.com/kgromero.vcf"})
    FILES["kgromero.vcf"] = b"x"
    assert not (await call({"document": "https://kgromero.com/kgromero.vcf"})).is_error
    assert by_url.is_error  # listed URL, but the fake fetch had no file yet
