"""Text extraction from PDF and office files (pure Python, no system tools)."""
from __future__ import annotations

import io
import re
import struct
import zipfile
import xml.etree.ElementTree as ET

from .http import SiteError, clean_text

W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
A = "{http://schemas.openxmlformats.org/drawingml/2006/main}"
R = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}"
PKG_REL = "{http://schemas.openxmlformats.org/package/2006/relationships}"
ODF_TEXT = "{urn:oasis:names:tc:opendocument:xmlns:text:1.0}"
OLE_MAGIC = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"
TEXT_EXT = {"txt", "csv", "tsv", "md", "json", "xml", "log", "py", "java", "js", "c", "cpp", "h", "sql", "ini", "yml", "yaml"}
IMAGE_EXT = {"png", "jpg", "jpeg", "gif", "bmp", "webp", "svg", "tif", "tiff"}
MAX_PAGES = 150
MAX_ROWS = 2000


def detect(data: bytes, filename: str = "", content_type: str = "") -> str:
    """Return one of: pdf docx pptx xlsx odf doc ppt xls html text image zip unknown."""
    if data[:5] == b"%PDF-":
        return "pdf"
    if data[:2] == b"PK":
        try:
            names = set(zipfile.ZipFile(io.BytesIO(data)).namelist())
        except zipfile.BadZipFile:
            return "unknown"
        for marker, kind in (("word/document.xml", "docx"), ("ppt/presentation.xml", "pptx"),
                             ("xl/workbook.xml", "xlsx"), ("content.xml", "odf")):
            if marker in names:
                return kind
        return "zip"
    if data[:8] == OLE_MAGIC:
        import olefile

        ole = olefile.OleFileIO(io.BytesIO(data))
        try:
            if ole.exists("WordDocument"):
                return "doc"
            if ole.exists("PowerPoint Document"):
                return "ppt"
            if ole.exists("Workbook") or ole.exists("Book"):
                return "xls"
        finally:
            ole.close()
        return "unknown"
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    ctype = content_type.lower()
    if ext in ("html", "htm") or "html" in ctype:
        return "html"
    if ext in IMAGE_EXT or ctype.startswith("image/"):
        return "image"
    if ext in TEXT_EXT or ctype.startswith("text/"):
        return "text"
    return "unknown"


# --- OOXML -----------------------------------------------------------------

def _w_paragraph(p: ET.Element) -> str:
    parts = []
    for el in p.iter():
        if el.tag == W + "t":
            parts.append(el.text or "")
        elif el.tag == W + "tab":
            parts.append("\t")
        elif el.tag in (W + "br", W + "cr"):
            parts.append("\n")
    return "".join(parts)


def _docx(data: bytes) -> str:
    root = ET.fromstring(zipfile.ZipFile(io.BytesIO(data)).read("word/document.xml"))
    out: list[str] = []

    def walk(parent: ET.Element) -> None:
        for child in parent:
            if child.tag == W + "p":
                text = _w_paragraph(child)
                if text.strip():
                    out.append(text)
            elif child.tag == W + "tbl":
                for row in child.findall(W + "tr"):
                    cells = []
                    for cell in row.findall(W + "tc"):
                        cell_text = " ".join(t for t in (_w_paragraph(p).strip() for p in cell.iter(W + "p")) if t)
                        cells.append(cell_text)
                    if any(cells):
                        out.append(" | ".join(cells))
            elif child.tag == W + "sdt":
                content = child.find(W + "sdtContent")
                if content is not None:
                    walk(content)

    body = root.find(W + "body")
    if body is not None:
        walk(body)
    return "\n".join(out)


def _rels(z: zipfile.ZipFile, path: str) -> dict[str, str]:
    if path not in z.namelist():
        return {}
    root = ET.fromstring(z.read(path))
    return {r.get("Id"): r.get("Target", "") for r in root.findall(PKG_REL + "Relationship")}


def _slide_lines(xml: bytes) -> list[str]:
    root = ET.fromstring(xml)
    lines = []
    for p in root.iter(A + "p"):
        text = "".join(t.text or "" for t in p.iter(A + "t")).strip()
        if text:
            lines.append(text)
    return lines


def _pptx(data: bytes) -> str:
    z = zipfile.ZipFile(io.BytesIO(data))
    pres = ET.fromstring(z.read("ppt/presentation.xml"))
    rels = _rels(z, "ppt/_rels/presentation.xml.rels")
    slides = []
    for sld in pres.iter("{http://schemas.openxmlformats.org/presentationml/2006/main}sldId"):
        target = rels.get(sld.get(R + "id"), "")
        if target:
            slides.append("ppt/" + target.lstrip("/").removeprefix("ppt/"))
    if not slides:
        slides = sorted((n for n in z.namelist() if re.fullmatch(r"ppt/slides/slide\d+\.xml", n)),
                        key=lambda n: int(re.findall(r"\d+", n)[-1]))
    out = []
    for number, path in enumerate(slides, 1):
        out.append(f"--- Folie {number} ---")
        out.extend(_slide_lines(z.read(path)))
        slide_rels = _rels(z, path.replace("slides/", "slides/_rels/") + ".rels")
        for target in slide_rels.values():
            if "notesSlide" in target:
                notes_path = "ppt/" + target.replace("../", "")
                if notes_path in z.namelist():
                    notes = [ln for ln in _slide_lines(z.read(notes_path)) if not ln.isdigit()]
                    if notes:
                        out.append("[Notizen] " + " ".join(notes))
    return "\n".join(out)


def _xlsx(data: bytes) -> str:
    import openpyxl

    wb = openpyxl.load_workbook(io.BytesIO(data), read_only=True, data_only=True)
    out = []
    for ws in wb.worksheets:
        out.append(f"--- Tabelle {ws.title} ---")
        for count, row in enumerate(ws.iter_rows(values_only=True)):
            if count >= MAX_ROWS:
                out.append("[weitere Zeilen abgeschnitten]")
                break
            cells = ["" if v is None else str(v) for v in row]
            if any(cells):
                out.append(" | ".join(cells).rstrip(" |"))
    wb.close()
    return "\n".join(out)


def _odf(data: bytes) -> str:
    root = ET.fromstring(zipfile.ZipFile(io.BytesIO(data)).read("content.xml"))
    lines = []
    for el in root.iter():
        if el.tag in (ODF_TEXT + "p", ODF_TEXT + "h"):
            text = "".join(el.itertext()).strip()
            if text:
                lines.append(text)
    return "\n".join(lines)


# --- PDF -------------------------------------------------------------------

def _pdf(data: bytes) -> str:
    from pypdf import PdfReader

    reader = PdfReader(io.BytesIO(data))
    if reader.is_encrypted and not reader.decrypt(""):
        raise SiteError("PDF ist passwortgeschützt.")
    out = []
    for number, page in enumerate(reader.pages[:MAX_PAGES], 1):
        text = (page.extract_text() or "").strip()
        out.append(f"--- Seite {number} ---\n{text}")
    if len(reader.pages) > MAX_PAGES:
        out.append(f"[nur die ersten {MAX_PAGES} von {len(reader.pages)} Seiten gelesen]")
    if not any(p.split("\n", 1)[-1].strip() for p in out if p.startswith("---")):
        out.append("[Keine Textebene gefunden: vermutlich gescanntes PDF oder nur Bilder.]")
    return "\n".join(out)


# --- legacy binary Office (OLE) --------------------------------------------

def _doc(data: bytes) -> str:
    import olefile

    ole = olefile.OleFileIO(io.BytesIO(data))
    try:
        word = ole.openstream("WordDocument").read()
        flags = struct.unpack_from("<H", word, 0x0A)[0]
        table = ole.openstream("1Table" if flags & 0x0200 else "0Table").read()
    finally:
        ole.close()
    fc_clx, lcb_clx = struct.unpack_from("<II", word, 0x01A2)
    ccp_text = struct.unpack_from("<i", word, 0x4C)[0]
    clx = table[fc_clx:fc_clx + lcb_clx]
    pos = 0
    while pos < len(clx) and clx[pos] == 1:
        pos += 3 + struct.unpack_from("<H", clx, pos + 1)[0]
    if pos >= len(clx) or clx[pos] != 2:
        raise SiteError("DOC-Format nicht lesbar (Piece-Table fehlt).")
    size = struct.unpack_from("<I", clx, pos + 1)[0]
    plc = clx[pos + 5:pos + 5 + size]
    pieces = (len(plc) - 4) // 12
    cps = struct.unpack_from(f"<{pieces + 1}I", plc, 0)
    chunks = []
    for i in range(pieces):
        fc = struct.unpack_from("<I", plc, 4 * (pieces + 1) + 8 * i + 2)[0]
        length = cps[i + 1] - cps[i]
        if fc & 0x40000000:
            start = (fc & 0x3FFFFFFF) // 2
            chunks.append(word[start:start + length].decode("cp1252", errors="replace"))
        else:
            chunks.append(word[fc:fc + length * 2].decode("utf-16-le", errors="replace"))
    text = "".join(chunks)
    if ccp_text > 0:
        text = text[:ccp_text]
    text = re.sub(r"\x13[^\x14\x15]*\x14", "", text)  # field instructions
    text = text.replace("\x07\x07", "\n").replace("\x07", " | ").replace("\r", "\n").replace("\x0b", "\n").replace("\x0c", "\n")
    text = re.sub(r"[\x00-\x08\x0e-\x1f]", "", text)
    text = re.sub(r"( \| )+\n", "\n", text)
    return "\n".join(ln.rstrip(" |").strip() for ln in text.split("\n") if ln.strip(" |"))


def _ppt(data: bytes) -> str:
    import olefile

    ole = olefile.OleFileIO(io.BytesIO(data))
    try:
        stream = ole.openstream("PowerPoint Document").read()
    finally:
        ole.close()
    out: list[str] = []
    counter = {"slide": 0}

    def walk(start: int, end: int) -> None:
        pos = start
        while pos + 8 <= end:
            ver_inst, rtype, length = struct.unpack_from("<HHI", stream, pos)
            body = pos + 8
            if body + length > end:
                break
            if rtype == 0x03F8:  # master slide: placeholder prompts only
                pass
            elif ver_inst & 0xF == 0xF:
                if rtype == 0x03EE:
                    counter["slide"] += 1
                    out.append(f"--- Folie {counter['slide']} ---")
                elif rtype == 0x03F0:
                    out.append("[Notizen]")
                walk(body, body + length)
            elif rtype == 0x0FA0:
                out.append(stream[body:body + length].decode("utf-16-le", errors="replace"))
            elif rtype == 0x0FA8:
                out.append(stream[body:body + length].decode("cp1252", errors="replace"))
            pos = body + length

    walk(0, len(stream))
    lines = [ln.strip() for ln in "\n".join(out).replace("\r", "\n").split("\n") if ln.strip() not in ("", "*")]
    text = re.sub(r"\[Notizen\]\n(?=\[Notizen\]|---)", "", "\n".join(lines))
    return re.sub(r"(\n?\[Notizen\])+\Z", "", text).strip()


def _xls(data: bytes) -> str:
    import xlrd

    book = xlrd.open_workbook(file_contents=data)
    out = []
    for sheet in book.sheets():
        out.append(f"--- Tabelle {sheet.name} ---")
        for r in range(min(sheet.nrows, MAX_ROWS)):
            cells = [str(c.value) if c.value != "" else "" for c in sheet.row(r)]
            if any(cells):
                out.append(" | ".join(cells).rstrip(" |"))
    return "\n".join(out)


# --- entry point -----------------------------------------------------------

def extract(data: bytes, filename: str = "", content_type: str = "") -> tuple[str, str]:
    """Return (kind, text). Raises SiteError for unreadable or unsupported files."""
    kind = detect(data, filename, content_type)
    try:
        if kind == "pdf":
            return kind, _pdf(data)
        if kind == "docx":
            return kind, _docx(data)
        if kind == "pptx":
            return kind, _pptx(data)
        if kind == "xlsx":
            return kind, _xlsx(data)
        if kind == "odf":
            return kind, _odf(data)
        if kind == "doc":
            return kind, _doc(data)
        if kind == "ppt":
            return kind, _ppt(data)
        if kind == "xls":
            return kind, _xls(data)
        if kind == "html":
            return kind, clean_text(data.decode("utf-8", errors="replace"), 10_000_000)
        if kind == "text":
            try:
                return kind, data.decode("utf-8")
            except UnicodeDecodeError:
                return kind, data.decode("cp1252", errors="replace")
    except SiteError:
        raise
    except Exception as exc:  # corrupt or exotic file
        raise SiteError(f"{kind.upper()}-Datei nicht lesbar ({exc.__class__.__name__}).") from exc
    if kind == "image":
        return kind, "[Bilddatei: kein Text extrahiert]"
    return kind, f"[Dateityp nicht unterstützt: {filename or content_type or 'unbekannt'}]"
