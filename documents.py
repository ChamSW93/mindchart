"""Turning uploaded files into plain text, using only tools built into macOS and Python.

Files are processed in memory (PDFs and Word files pass briefly through a temporary
file that is deleted straight away). Nothing is sent off this computer.
"""
import csv
import datetime
import io
import re
import subprocess
import tempfile
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path

HERE = Path(__file__).parent
PDFTEXT = HERE / "tools" / "pdftext"
TEXTUTIL_TYPES = {".docx", ".doc", ".rtf", ".odt", ".html", ".htm", ".webarchive"}
# Photos, scans and faxes, read with the built-in OCR. TIFFs from fax systems can hold several pages.
IMAGE_TYPES = {".png", ".jpg", ".jpeg", ".heic", ".heif", ".tif", ".tiff", ".gif", ".bmp"}
MAX_CHAT_FILE_CHARS = 40000  # about 10,000 tokens, leaving room for the conversation
PAGE_MARKER = re.compile(r"^=== Page (\d+)( \(scanned(, unclear)?\))? ===$", re.M)
MIN_PICTURE_BYTES = 15000  # pictures in Word files smaller than this are logos and lines, not scanned pages
SPREADSHEET_NS = {
    "m": "http://schemas.openxmlformats.org/spreadsheetml/2006/main",
    "r": "http://schemas.openxmlformats.org/officeDocument/2006/relationships",
    "rel": "http://schemas.openxmlformats.org/package/2006/relationships",
}
BUILTIN_DATE_FORMATS = set(range(14, 23)) | set(range(45, 48))


def read_pages(name, data):
    """Read a file into pages: [{"page": 1, "text": "...", "scanned": False, "unclear": False}, ...].

    PDFs and images keep their page numbers so the history can cite them. Pages read with OCR are
    "scanned", and ones the OCR was unsure of (handwriting, poor copies) are also "unclear". Word and
    other files have no fixed pages, so they come back as a single page with "page": None.
    """
    suffix = Path(name).suffix.lower()
    if suffix == ".pdf" or suffix in IMAGE_TYPES:
        pages = parse_pages(run_on_temp_file(name, data, suffix, lambda path: [str(pdftext()), path]))
        if not any(p["text"] for p in pages):
            raise ValueError(f"No text found in {name}, even after trying to read it as a scan")
        return pages
    scanned = False
    if suffix in TEXTUTIL_TYPES:  # Word, RTF, etc. via macOS's built-in textutil
        text = run_on_temp_file(name, data, suffix, lambda path: ["textutil", "-convert", "txt", "-stdout", path])
        if suffix == ".docx":  # scanned pages pasted into Word arrive as pictures, so read those too
            pictures = text_in_pictures(data)
            if pictures.strip():
                text = f"{text.strip()}\n\n[Text read from pictures in the document]\n{pictures.strip()}"
                scanned = True
    elif suffix in (".xlsx", ".xlsm"):
        try:
            text = xlsx_to_text(data)
        except Exception:
            raise ValueError(f"Couldn't read {name} as an Excel file")
    elif suffix == ".xls":
        raise ValueError(f"{name} is an old-style Excel file. Open it in Excel or Numbers and save it as .xlsx or .csv")
    else:
        try:
            text = data.decode("utf-8-sig")  # -sig drops the marker Excel puts at the start of UTF-8 CSVs
        except UnicodeDecodeError:
            if suffix not in (".csv", ".tsv"):
                raise ValueError(f"{name} isn't a file type I can read")
            text = data.decode("cp1252", errors="replace")  # older Excel CSV exports on Windows
    if not text.strip():
        raise ValueError(f"No text found in {name}")
    return [{"page": None, "text": text.strip(), "scanned": scanned, "unclear": False}]


def parse_pages(output):
    """The reader's output, split at its page markers."""
    parts = PAGE_MARKER.split(output)
    # split() gives: [before, number, scanned, unclear, text, number, scanned, unclear, text, ...]
    return [{"page": int(parts[i]), "text": parts[i + 3].strip(), "scanned": bool(parts[i + 1]), "unclear": bool(parts[i + 2])}
            for i in range(1, len(parts), 4)]


def text_in_pictures(data):
    """OCR the larger pictures inside a Word (.docx) file, in the order they're stored."""
    texts = []
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            pictures = [i for i in z.infolist() if i.filename.startswith("word/media/")
                        and Path(i.filename).suffix.lower() in IMAGE_TYPES and i.file_size >= MIN_PICTURE_BYTES]
            for item in sorted(pictures, key=lambda i: i.filename):
                output = run_on_temp_file(item.filename, z.read(item), Path(item.filename).suffix.lower(),
                                          lambda path: [str(pdftext()), path])
                texts += [p["text"] for p in parse_pages(output) if p["text"]]
    except (zipfile.BadZipFile, ValueError):
        return ""
    return "\n\n".join(texts)


def extract_text(name, data):
    """A file's text as one string, cut to a size that fits in a chat message."""
    return pages_text(read_pages(name, data))


def pages_text(pages):
    if pages[0]["page"] is None:
        text = pages[0]["text"]
    else:
        text = "\n\n".join(f"=== Page {p['page']} ===\n{p['text']}" for p in pages)
    if len(text) > MAX_CHAT_FILE_CHARS:
        text = text[:MAX_CHAT_FILE_CHARS] + "\n\n[...file cut off here because it's too long...]"
    return text


def combine(files):
    """Photos, scans and PDFs (as [(file name, bytes)]) joined in order into one PDF, as bytes: the pages of one
    document that came in as separate files, such as photos of handwritten notes."""
    if any(Path(name).suffix.lower() not in IMAGE_TYPES | {".pdf"} for name, _ in files):
        raise ValueError("Only photos, scans and PDFs can be combined into one document")
    with tempfile.TemporaryDirectory() as folder:
        paths = []
        for n, (name, data) in enumerate(files):
            path = Path(folder) / f"{n:03d}{Path(name).suffix.lower()}"
            path.write_bytes(data)
            paths.append(str(path))
        combined = Path(folder) / "combined.pdf"
        subprocess.run([str(pdftext()), "--combine", str(combined), *paths], check=True, capture_output=True)
        return combined.read_bytes()


def has_pages(name):
    """Whether a file has pages that can be shown as pictures (PDFs, scans and photos, not Word or text files)."""
    return Path(name).suffix.lower() in IMAGE_TYPES | {".pdf"}


def render_page(name, data, page, folder=None):
    """One page of a PDF or picture as a PNG, to check its text against. folder: where the temporary copies go (the
    encrypted store, so a patient's page never sits unencrypted on disk)."""
    suffix = Path(name).suffix.lower()
    if not has_pages(name):
        raise ValueError("Only PDFs, scans and photos have pages to show")
    with tempfile.TemporaryDirectory(dir=folder) as temp:
        source, picture = Path(temp) / f"source{suffix}", Path(temp) / "page.png"
        source.write_bytes(data)
        result = subprocess.run([str(pdftext()), "--render", str(source), str(int(page)), str(picture)], capture_output=True)
        if result.returncode != 0 or not picture.exists():
            raise ValueError(f"Couldn't show page {page}")
        return picture.read_bytes()


def pdftext():
    """The PDF and image reader in tools/, built from its Swift source the first time it's needed."""
    if not PDFTEXT.exists():
        subprocess.run(["swiftc", "-O", str(PDFTEXT) + ".swift", "-o", str(PDFTEXT)], check=True)
    return PDFTEXT


def run_on_temp_file(name, data, suffix, command):
    with tempfile.NamedTemporaryFile(suffix=suffix) as f:  # deleted when the block ends
        f.write(data)
        f.flush()
        result = subprocess.run(command(f.name), capture_output=True, text=True)
    if result.returncode != 0:
        raise ValueError(f"Couldn't read {name}")
    return result.stdout


def column_index(cell_ref):
    """'C7' -> 2 (zero-based column number)."""
    letters = re.match(r"[A-Z]+", cell_ref).group()
    index = 0
    for letter in letters:
        index = index * 26 + ord(letter) - ord("A") + 1
    return index - 1


def xlsx_to_text(data):
    """Read every sheet of an Excel .xlsx file and return it as CSV text.

    An .xlsx file is a zip of XML files: the workbook lists the sheets, each
    sheet holds the cells, and repeated text is stored once in sharedStrings.
    """
    ns = SPREADSHEET_NS
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        files = set(z.namelist())

        shared = []
        if "xl/sharedStrings.xml" in files:
            for item in ET.fromstring(z.read("xl/sharedStrings.xml")).findall("m:si", ns):
                shared.append("".join(t.text or "" for t in item.iter(f"{{{ns['m']}}}t")))

        # Excel stores dates as numbers; the cell's style says whether to show it as a date.
        date_styles = set()
        if "xl/styles.xml" in files:
            styles = ET.fromstring(z.read("xl/styles.xml"))
            custom_dates = {
                int(f.get("numFmtId"))
                for f in styles.findall("m:numFmts/m:numFmt", ns)
                if re.search(r"[dmy]", re.sub(r'"[^"]*"|\[[^]]*]', "", f.get("formatCode", "")), re.I)
            }
            for i, xf in enumerate(styles.findall("m:cellXfs/m:xf", ns)):
                fmt = int(xf.get("numFmtId", 0))
                if fmt in BUILTIN_DATE_FORMATS or fmt in custom_dates:
                    date_styles.add(i)

        rels = ET.fromstring(z.read("xl/_rels/workbook.xml.rels"))
        targets = {r.get("Id"): r.get("Target") for r in rels.findall("rel:Relationship", ns)}
        workbook = ET.fromstring(z.read("xl/workbook.xml"))

        output = []
        for sheet in workbook.findall("m:sheets/m:sheet", ns):
            target = targets[sheet.get(f"{{{ns['r']}}}id")].lstrip("/")
            path = target if target.startswith("xl/") else "xl/" + target
            rows = []
            for row in ET.fromstring(z.read(path)).iter(f"{{{ns['m']}}}row"):
                values = []
                for cell in row.findall("m:c", ns):
                    kind = cell.get("t")
                    v = cell.find("m:v", ns)
                    value = v.text if v is not None and v.text is not None else ""
                    if kind == "s" and value:
                        value = shared[int(value)]
                    elif kind == "inlineStr":
                        value = "".join(t.text or "" for t in cell.iter(f"{{{ns['m']}}}t"))
                    elif kind == "b":
                        value = "TRUE" if value == "1" else "FALSE"
                    elif value and int(cell.get("s", 0)) in date_styles:
                        moment = datetime.datetime(1899, 12, 30) + datetime.timedelta(days=float(value))
                        value = moment.strftime("%Y-%m-%d %H:%M" if moment.hour or moment.minute else "%Y-%m-%d")
                    ref = cell.get("r")
                    column = column_index(ref) if ref else len(values)
                    values.extend([""] * (column - len(values)))  # blank cells aren't stored
                    values.append(value)
                if any(values):
                    rows.append(values)
            if rows:
                buffer = io.StringIO()
                csv.writer(buffer).writerows(rows)
                output.append(f"Sheet: {sheet.get('name')}\n{buffer.getvalue()}")
    return "\n".join(output)
