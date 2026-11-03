"""Shared helpers for the prover-verifier-paper scripts. Standard library only."""
from __future__ import annotations

import bisect
import datetime
import json
import os
import re
import tempfile
import unicodedata
from difflib import SequenceMatcher
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent
SKILL_DIR = SCRIPTS_DIR.parent

# ---------------------------------------------------------------- files


def now_iso() -> str:
    return datetime.datetime.now().astimezone().isoformat(timespec="seconds")


def today() -> str:
    return datetime.date.today().isoformat()


def read_json(path, default=None):
    p = Path(path)
    if not p.exists():
        if default is not None:
            return default
        raise FileNotFoundError(str(p))
    with p.open(encoding="utf-8") as f:
        return json.load(f)


def _atomic_write(path, data: str):
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(p.parent), prefix=".tmp-")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(data)
    os.replace(tmp, p)


def write_json(path, obj):
    _atomic_write(path, json.dumps(obj, indent=2, ensure_ascii=False) + "\n")


def write_text(path, text: str):
    _atomic_write(path, text)


def extract_json(text: str):
    """Parse JSON from a role agent's output: raw JSON, a ```json fence, or JSON embedded in prose."""
    text = text.strip()
    if not text:
        raise ValueError("empty output")
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    for m in re.finditer(r"```(?:json)?\s*\n(.*?)```", text, re.S):
        try:
            return json.loads(m.group(1))
        except json.JSONDecodeError:
            continue
    dec = json.JSONDecoder()
    for i, ch in enumerate(text):
        if ch in "[{":
            try:
                obj, _ = dec.raw_decode(text[i:])
                return obj
            except json.JSONDecodeError:
                continue
    raise ValueError("no JSON object or array found")


# ---------------------------------------------------------------- text matching

_TRANS = str.maketrans({
    "\u2018": "'", "\u2019": "'", "\u201c": '"', "\u201d": '"',
    "\u2013": "-", "\u2014": "-", "\u2212": "-", "\u00a0": " ", "\u00ad": "",
})
_LATEX_CMD = re.compile(r"\\[A-Za-z@]+\*?")
_NON_ALNUM = re.compile(r"[^a-z0-9]+")


def loose(s: str) -> str:
    """Aggressive normalisation for quote matching: drop case, whitespace, punctuation, accents,
    hyphenation, ligatures and LaTeX command names. '84.2\\%' and '84.2%' both become '842';
    'Gómez' and the PDF artefact 'G´omez' both become 'gomez'."""
    s = unicodedata.normalize("NFKC", s).translate(_TRANS)
    s = _LATEX_CMD.sub(" ", s)
    s = "".join(ch for ch in unicodedata.normalize("NFKD", s) if not unicodedata.combining(ch))
    return _NON_ALNUM.sub("", s.casefold())


LOC_RE = re.compile(r"(?<![\d.])L?\s*(\d{1,6})(?:\s*(?:-|–|—|to|:)\s*L?\s*(\d{1,6}))?", re.I)


def parse_loc(loc):
    """'L412-L414', 'L412–414', 'paper.md:412-414', 'lines 12 to 18', 'L7' -> (start, end) 1-based."""
    if loc is None:
        return None
    if isinstance(loc, (list, tuple)) and loc:
        try:
            a = int(loc[0]); b = int(loc[-1])
            return (min(a, b), max(a, b))
        except (TypeError, ValueError):
            return None
    s = str(loc)
    s = re.sub(r"\.(md|tex|txt)\s*:", " ", s)
    m = LOC_RE.search(s)
    if not m:
        return None
    a = int(m.group(1))
    b = int(m.group(2)) if m.group(2) else a
    return (min(a, b), max(a, b))


class TextIndex:
    """Line-anchored text with fast loose-substring and fuzzy quote lookup."""

    def __init__(self, text: str):
        self.lines = text.splitlines()
        self._loose_lines = [loose(x) for x in self.lines]
        self._starts = []
        pos = 0
        for ll in self._loose_lines:
            self._starts.append(pos)
            pos += len(ll)
        self._loose = "".join(self._loose_lines)

    @classmethod
    def from_file(cls, path):
        return cls(Path(path).read_text(encoding="utf-8", errors="replace"))

    def __len__(self):
        return len(self.lines)

    def line_of(self, offset: int) -> int:
        """1-based line number containing loose-text offset."""
        # bisect_right picks the last line whose start <= offset; blank lines have zero
        # width, so the character at `offset` belongs to that line.
        i = bisect.bisect_right(self._starts, offset) - 1
        return max(1, i + 1)

    def _range_offsets(self, start: int, end: int):
        if not self.lines:
            return 0, 0
        n = len(self.lines)
        start = max(1, min(start, n))
        end = max(start, min(end, n))
        a = self._starts[start - 1]
        b = self._starts[end - 1] + len(self._loose_lines[end - 1])
        return a, b

    def snippet(self, start: int, end: int, max_chars: int = 600) -> str:
        start = max(1, start); end = min(len(self.lines), end)
        out = "\n".join(self.lines[start - 1:end])
        return out if len(out) <= max_chars else out[:max_chars] + " …"

    def find(self, quote: str, loc=None, window: int = 8, fuzzy_threshold: float = 0.6):
        """Locate a quote (after loose normalisation). Returns dict(status, line, score):
        ok (found at/near loc), moved (found elsewhere), fuzzy (only an approximate match: the line
        of the closest text is returned as a hint), missing. Callers must treat fuzzy as NOT found —
        a one-digit difference ('81.4' vs '91.4') or a dropped 'not' still scores high."""
        q = loose(quote or "")
        if len(q) < 6:
            return {"status": "missing", "line": None, "score": 0.0, "note": "quote too short to check"}
        span = parse_loc(loc)
        # 1) exact (loose) near loc, 2) exact anywhere
        if span:
            a, b = self._range_offsets(span[0] - window, span[1] + window)
            i = self._loose.find(q, a, b + len(q))  # match must start inside the window
            if i != -1:
                return {"status": "ok", "line": self.line_of(i), "score": 1.0}
        i = self._loose.find(q)
        if i != -1:
            line = self.line_of(i)
            status = "ok" if not span else ("ok" if span[0] - window <= line <= span[1] + window else "moved")
            return {"status": status, "line": line, "score": 1.0}
        # 3) fuzzy: anchor on the longest common block, then score the aligned window
        best = (0.0, None)
        regions = []
        if span:
            regions.append(self._range_offsets(span[0] - window, span[1] + window))
        regions.append((0, len(self._loose)))
        for a, b in regions:
            hay = self._loose[a:b]
            if not hay:
                continue
            sm = SequenceMatcher(None, hay, q, autojunk=False)
            m = sm.find_longest_match(0, len(hay), 0, len(q))
            if m.size < 8:
                continue
            ws = max(0, m.a - m.b)
            cand = hay[ws: ws + len(q)]
            score = SequenceMatcher(None, cand, q, autojunk=False).ratio()
            if score > best[0]:
                best = (score, a + ws)
            if best[0] >= fuzzy_threshold:
                break
        if best[1] is not None and best[0] >= fuzzy_threshold:
            return {"status": "fuzzy", "line": self.line_of(best[1]), "score": round(best[0], 3)}
        return {"status": "missing", "line": None, "score": round(best[0], 3)}

    def search(self, pattern: str, start: int = 1, end=None, max_hits: int = 5):
        """Case-insensitive regex search over raw lines; returns (count, [line numbers])."""
        rx = re.compile(pattern, re.I)
        end = len(self.lines) if end is None else min(end, len(self.lines))
        hits = [i + 1 for i in range(max(0, start - 1), end) if rx.search(self.lines[i])]
        return len(hits), hits[:max_hits]


def short(s, n=160):
    s = " ".join(str(s or "").split())
    return s if len(s) <= n else s[: n - 1] + "…"


# ---------------------------------------------------------------- network + archives

USER_AGENT = "prover-verifier-paper-skill/1.0 (research paper claim audit)"


def http_get(url: str, timeout: int = 60, retries: int = 3) -> bytes:
    """GET with retries, a certifi fallback for macOS Python SSL issues, and a curl fallback."""
    import shutil
    import ssl
    import subprocess
    import time
    import urllib.error
    import urllib.request

    contexts = [None]
    try:
        import certifi  # type: ignore
        contexts.append(ssl.create_default_context(cafile=certifi.where()))
    except Exception:  # noqa: BLE001
        pass
    last = None
    for attempt in range(retries):
        for ctx in contexts:
            try:
                req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
                with urllib.request.urlopen(req, timeout=timeout, context=ctx) as r:
                    return r.read()
            except urllib.error.HTTPError as e:
                last = e
                if e.code in (401, 403, 404, 410):
                    raise
                break  # 429/5xx: back off, then retry
            except (urllib.error.URLError, ssl.SSLError, TimeoutError, OSError) as e:
                last = e
        time.sleep(2.0 * (attempt + 1))
    if shutil.which("curl"):
        p = subprocess.run(["curl", "-sSfL", "--max-time", str(timeout), "-A", USER_AGENT, url],
                           capture_output=True)
        if p.returncode == 0 and p.stdout:
            return p.stdout
    raise RuntimeError(f"download failed for {url}: {last}")


def safe_extract(data: bytes, dest: Path, max_total: int = 300 * 1024 * 1024) -> list:
    """Extract a tar/zip archive from bytes without path traversal or links. Returns file paths."""
    import io
    import shutil
    import tarfile
    import zipfile

    dest = Path(dest).resolve()
    dest.mkdir(parents=True, exist_ok=True)
    written, total = [], 0

    def target_for(name: str):
        name = name.replace("\\", "/").lstrip("/")
        if not name or any(part == ".." for part in name.split("/")):
            return None
        t = (dest / name).resolve()
        return t if str(t).startswith(str(dest)) else None

    bio = io.BytesIO(data)
    if zipfile.is_zipfile(bio):
        with zipfile.ZipFile(bio) as zf:
            for info in zf.infolist():
                t = target_for(info.filename)
                if t is None or info.is_dir():
                    continue
                total += info.file_size
                if total > max_total:
                    raise RuntimeError("archive too large")
                t.parent.mkdir(parents=True, exist_ok=True)
                with zf.open(info) as src, open(t, "wb") as out:
                    shutil.copyfileobj(src, out)
                written.append(t)
        return written
    bio.seek(0)
    with tarfile.open(fileobj=bio, mode="r:*") as tf:
        for m in tf.getmembers():
            if not m.isfile():
                continue
            t = target_for(m.name)
            if t is None:
                continue
            total += m.size
            if total > max_total:
                raise RuntimeError("archive too large")
            t.parent.mkdir(parents=True, exist_ok=True)
            src = tf.extractfile(m)
            if src is None:
                continue
            with src, open(t, "wb") as out:
                shutil.copyfileobj(src, out)
            written.append(t)
    return written


def pdf_pages(path) -> tuple:
    """Return (list of page texts, extractor name). Tries PyMuPDF, pdftotext, then pypdf."""
    import shutil
    import subprocess

    try:
        try:
            import pymupdf as fitz  # type: ignore
        except ImportError:
            import fitz  # type: ignore
        with fitz.open(str(path)) as doc:
            return [p.get_text("text") for p in doc], "pymupdf"
    except ImportError:
        pass
    if shutil.which("pdftotext"):
        p = subprocess.run(["pdftotext", "-enc", "UTF-8", str(path), "-"], capture_output=True)
        if p.returncode == 0:
            pages = p.stdout.decode("utf-8", "replace").split("\f")
            if pages and not pages[-1].strip():
                pages = pages[:-1]
            return pages, "pdftotext"
    try:
        import pypdf  # type: ignore
        r = pypdf.PdfReader(str(path))
        return [(pg.extract_text() or "") for pg in r.pages], "pypdf"
    except ImportError:
        pass
    raise RuntimeError("no PDF text extractor found: pip install pymupdf (or install poppler's pdftotext)")
