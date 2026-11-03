#!/usr/bin/env python3
"""Ingest a paper into a line-anchored audit folder (prover-verifier-paper skill).

  ingest.py SOURCE [--out DIR] [--prefer latex|pdf] [--with-pdf] [--force]

SOURCE: an arXiv id or URL (2605.25133, arXiv:2605.25133v1, https://arxiv.org/abs/...),
a PDF URL, a local .pdf, a .tex file or LaTeX folder, a LaTeX source archive
(.tar.gz/.tgz/.tar/.zip/.gz), or a .md/.txt file.

Writes DIR/paper/:
  paper.md         line-anchored text (LaTeX kept for tables/math; numbered markdown headings)
  meta.json        title, authors, sections with line ranges, extractor, integrity findings
  references.json  bibliography entries with keys, labels, titles, arXiv/DOI/ACL ids
  raw/             downloaded or copied originals
Default DIR is ./pvd-audit/<slug>. arXiv papers use the LaTeX source when available
(exact tables and citation keys) and fall back to the PDF.
"""
from __future__ import annotations

import argparse
import gzip
import io
import json
import re
import shutil
import sys
import tarfile
import unicodedata
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _common import (TextIndex, http_get, now_iso, pdf_pages, safe_extract,  # noqa: E402
                     write_json, write_text)

ARXIV_NEW = re.compile(r"(?<![\d.])(\d{4}\.\d{4,5})(v\d+)?(?![\d])")
ARXIV_OLD = re.compile(r"\b([a-z\-]+(?:\.[A-Z]{2})?/\d{7})(v\d+)?\b")

# ================================================================ source resolution


def arxiv_id_from(s: str):
    s = s.strip()
    if re.match(r"^(arxiv:)?\d{4}\.\d{4,5}(v\d+)?$", s, re.I) or "arxiv.org" in s.lower():
        m = ARXIV_NEW.search(s)
        if m:
            return m.group(1) + (m.group(2) or "")
        m = ARXIV_OLD.search(s)
        if m:
            return m.group(1) + (m.group(2) or "")
    m = re.match(r"^arxiv:([a-z\-]+(?:\.[A-Z]{2})?/\d{7}(?:v\d+)?)$", s, re.I)
    if m:
        return m.group(1)
    return None


def slugify(s: str) -> str:
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode()
    s = re.sub(r"[^A-Za-z0-9]+", "-", s).strip("-").lower()
    return s[:60] or "paper"


def arxiv_metadata(aid: str) -> dict:
    import xml.etree.ElementTree as ET
    base = aid.split("v")[0] if re.match(r"^\d{4}\.\d{4,5}v\d+$", aid) else aid
    data = http_get(f"https://export.arxiv.org/api/query?id_list={base}", timeout=30)
    ns = {"a": "http://www.w3.org/2005/Atom"}
    root = ET.fromstring(data)
    e = root.find("a:entry", ns)
    if e is None:
        return {}
    def txt(tag):
        x = e.find(tag, ns)
        return " ".join(x.text.split()) if x is not None and x.text else None
    return {"title": txt("a:title"), "abstract": txt("a:summary"), "published": txt("a:published"),
            "authors": [" ".join(a.find("a:name", ns).text.split()) for a in e.findall("a:author", ns)
                        if a.find("a:name", ns) is not None]}


# ================================================================ LaTeX


def strip_comments(tex: str) -> str:
    out = []
    for line in tex.split("\n"):
        cut, i = None, 0
        while True:
            j = line.find("%", i)
            if j == -1:
                break
            k, bs = j - 1, 0
            while k >= 0 and line[k] == "\\":
                bs += 1
                k -= 1
            if bs % 2 == 0:
                cut = j
                break
            i = j + 1
        out.append(line if cut is None else line[:cut])
    return "\n".join(out)


def remove_iffalse(tex: str) -> str:
    """Drop \\iffalse ... \\fi blocks (keeping an \\else branch). Fails safe: returns input if unbalanced."""
    tok = re.compile(r"\\(iffalse|if[a-zA-Z@]*|else|fi)(?![a-zA-Z@])")
    out, pos, skip, drop_fi = [], 0, 0, []
    for m in tok.finditer(tex):
        name = m.group(1)
        if name == "ifthenelse" or tex[max(0, m.start() - 6):m.start()] == "\\newif":
            continue
        if skip:
            if name.startswith("if"):
                skip += 1
            elif name == "fi":
                skip -= 1
                if skip == 0:
                    pos = m.end()
            elif name == "else" and skip == 1:
                skip, pos = 0, m.end()
                drop_fi.append(True)
            continue
        if name == "iffalse":
            out.append(tex[pos:m.start()])
            skip = 1
        elif name.startswith("if"):
            drop_fi.append(False)
        elif name == "fi" and drop_fi:
            if drop_fi.pop():
                out.append(tex[pos:m.start()])
                pos = m.end()
    if skip:
        return tex
    out.append(tex[pos:])
    return "".join(out)


def balanced(s: str, start: int, open_="{", close="}"):
    """Given s[start] == open_, return index of the matching close (or -1)."""
    depth = 0
    i = start
    while i < len(s):
        c = s[i]
        if c == "\\":
            i += 2
            continue
        if c == open_:
            depth += 1
        elif c == close:
            depth -= 1
            if depth == 0:
                return i
        i += 1
    return -1


def arg_after(s: str, pos: int):
    """Return (argument text, end index) of the first {...} group at/after pos (skipping spaces)."""
    while pos < len(s) and s[pos] in " \t\n":
        pos += 1
    if pos >= len(s) or s[pos] != "{":
        return None, pos
    end = balanced(s, pos)
    if end == -1:
        return None, pos
    return s[pos + 1:end], end + 1


INPUT_RE = re.compile(r"\\(?:input|include|subfile)\s*\{\s*([^}]+?)\s*\}")
IMPORT_RE = re.compile(r"\\(sub)?(?:import|includefrom|inputfrom)\*?\s*\{\s*([^}]*?)\s*\}\s*\{\s*([^}]+?)\s*\}")


def flatten(path: Path, root: Path, depth=0, seen=None) -> str:
    seen = seen if seen is not None else set()
    seen.add(path.resolve())
    text = strip_comments(path.read_text(encoding="utf-8", errors="replace"))

    def resolve(name: str, base: Path):
        for cand in (base / name, base / (name + ".tex"), root / name, root / (name + ".tex")):
            if cand.is_file() and cand.resolve() not in seen:
                return cand
        return None

    def rep_input(m):
        if depth > 20:
            return m.group(0)
        c = resolve(m.group(1), path.parent)
        return ("\n" + flatten(c, root, depth + 1, seen) + "\n") if c else m.group(0)

    def rep_import(m):
        if depth > 20:
            return m.group(0)
        base = (path.parent if m.group(1) else root) / m.group(2)
        c = resolve(m.group(3), base)
        return ("\n" + flatten(c, root, depth + 1, seen) + "\n") if c else m.group(0)

    text = IMPORT_RE.sub(rep_import, text)
    return INPUT_RE.sub(rep_input, text)


NEWCMD_RE = re.compile(r"\\(?:re|provide)?(?:newcommand|command)\*?\s*\{?\s*\\([A-Za-z@]+)\s*\}?\s*(?:\[(\d)\])?\s*(?=\{)")
DEF_RE = re.compile(r"\\def\s*\\([A-Za-z@]+)\s*(?=\{)")


def collect_macros(tex: str) -> dict:
    macros = {}
    for rx in (NEWCMD_RE, DEF_RE):
        for m in rx.finditer(tex):
            nargs = m.group(2) if rx is NEWCMD_RE else None
            if nargs not in (None, "0"):
                continue
            body, _ = arg_after(tex, m.end())
            if body is None or "#" in body or len(body) > 200:
                continue
            body = body.replace("\\xspace", "").strip()
            if body and m.group(1) not in ("item", "section", "caption", "label", "cite", "ref"):
                macros[m.group(1)] = body
    return macros


def expand_macros(tex: str, macros: dict) -> str:
    if not macros:
        return tex
    names = sorted(macros, key=len, reverse=True)
    rx = re.compile(r"\\(" + "|".join(re.escape(n) for n in names) + r")(?![A-Za-z@])(\{\})?")
    for _ in range(3):
        new = rx.sub(lambda m: macros[m.group(1)], tex)
        if new == tex:
            break
        tex = new
    return tex.replace("\\xspace", "")


SECTION_RE = re.compile(r"\\(section|subsection|subsubsection|paragraph)(\*?)\s*(?:\[[^\]]*\])?\s*(?=\{)")
ENV_RE = re.compile(r"\\begin\{(table\*?|figure\*?|abstract|thebibliography|comment)\}")


def _letters(n: int) -> str:
    s = ""
    while n > 0:
        n, r = divmod(n - 1, 26)
        s = chr(65 + r) + s
    return s


def latex_to_markdown(body: str):
    """Number sections, convert headings to markdown, and mark tables/figures."""
    body = re.sub(r"\\begin\{comment\}.*?\\end\{comment\}", "", body, flags=re.S)
    counters = [0, 0, 0]
    appendix = False
    out = []
    pos = 0
    tcount = fcount = 0
    token = re.compile(SECTION_RE.pattern + r"|\\appendix\b|\\begin\{(table\*?|figure\*?|abstract)\}|\\maketitle\b")
    for m in token.finditer(body):
        if m.start() < pos:
            continue
        out.append(body[pos:m.start()])
        s = m.group(0)
        if s.startswith("\\appendix"):
            appendix = True
            counters = [0, 0, 0]
            out.append("\n\n## Appendix\n")
            pos = m.end()
            continue
        if s.startswith("\\maketitle"):
            out.append("\n<!-- maketitle -->\n")
            pos = m.end()
            continue
        if s.startswith("\\begin"):
            env = m.group(3)
            if env.startswith("abstract"):
                out.append("\n## Abstract\n")
            elif env.startswith("table"):
                tcount += 1
                out.append(f"\n<!-- TABLE T{tcount} -->\n" + s)
            else:
                fcount += 1
                out.append(f"\n<!-- FIGURE F{fcount} -->\n" + s)
            pos = m.end()
            continue
        level = ["section", "subsection", "subsubsection", "paragraph"].index(m.group(1))
        title, end = arg_after(body, m.end())
        if title is None:
            out.append(s)
            pos = m.end()
            continue
        title = " ".join(title.split())
        if level == 3:
            out.append(f"\n\n**{title}**\n")
        else:
            num = ""
            if not m.group(2):
                counters[level] += 1
                for k in range(level + 1, 3):
                    counters[k] = 0
                first = _letters(counters[0]) if appendix else str(counters[0])
                num = ".".join([first] + [str(c) for c in counters[1:level + 1]]) + " "
            out.append(f"\n\n{'#' * (level + 2)} {num}{title}\n")
        pos = end
    out.append(body[pos:])
    text = "".join(out)
    text = re.sub(r"\\end\{abstract\}", "", text)
    return text, tcount, fcount


FLOAT_RE = re.compile(r"<!-- (TABLE|FIGURE) ([TF]\d+) -->\n(\\begin\{(?:table|figure)\*?\}.*?\\end\{(?:table|figure)\*?\})", re.S)


def annotate_floats(md: str) -> str:
    """Put each table/figure's label and caption into its marker so agents can grep for them."""
    def rep(m):
        env = m.group(3)
        bits = [f"{m.group(1)} {m.group(2)}"]
        lab = re.search(r"\\label\{([^}]+)\}", env)
        if lab:
            bits.append(f"label: {lab.group(1)}")
        cm = re.search(r"\\caption\s*(?:\[[^\]]*\])?\s*(?=\{)", env)
        if cm:
            cap, _ = arg_after(env, cm.end())
            if cap:
                bits.append("caption: " + clean_tex(cap)[:180].replace("--", "–"))
        return "<!-- " + " · ".join(bits) + " -->\n" + env
    return FLOAT_RE.sub(rep, md)


def resolve_refs(md: str) -> str:
    """Replace \\ref{label} with the number the PDF would show (sections, tables, figures)."""
    labels = {}
    cur_sec, in_float, float_kind, float_num = None, False, None, None
    for line in md.split("\n"):
        m = re.match(r"^#{2,4} ((?:\d+|[A-Z])(?:\.\d+)*) ", line)
        if m:
            cur_sec = m.group(1)
        f = re.match(r"<!-- (TABLE|FIGURE) [TF](\d+)", line)
        if f:
            in_float, float_kind, float_num = True, f.group(1).title(), f.group(2)
        for lab in re.findall(r"\\label\{([^}]+)\}", line):
            if in_float:
                labels[lab] = (float_kind, float_num)
            elif cur_sec:
                labels[lab] = ("Appendix" if cur_sec[0].isalpha() else "Section", cur_sec)
        if re.search(r"\\end\{(?:table|figure)\*?\}", line):
            in_float = False

    def rep(m):
        keys = [k.strip() for k in m.group(2).split(",")]
        found = [labels.get(k) for k in keys]
        if not all(found):
            return m.group(0)
        if m.group(1) in ("autoref", "cref", "Cref"):
            return ", ".join(f"{kind} {num}" for kind, num in found)
        return ", ".join(num for _, num in found)
    md = re.sub(r"\\(ref|autoref|cref|Cref)\{([^}]+)\}", rep, md)
    return re.sub(r"(Table|Tab\.|Section|Sec\.|Figure|Fig\.|Appendix|App\.|Eq\.)~", r"\1 ", md)


def wrap_long_lines(text: str, limit: int = 600) -> str:
    out = []
    for line in text.split("\n"):
        while len(line) > limit:
            cut = -1
            for m in re.finditer(r"(?<=[.!?])\s+(?=[A-Z\\(\[])", line[: limit + 200]):
                if m.start() > 80:
                    cut = m.start()
                    if cut >= limit // 2:
                        break
            if cut == -1:
                cut = line.rfind(" ", 0, limit)
            if cut <= 0:
                break
            out.append(line[:cut].rstrip())
            line = line[cut:].lstrip()
        out.append(line)
    text = "\n".join(out)
    return re.sub(r"\n{4,}", "\n\n\n", text)


# ---------------------------------------------------------------- bibliography

ACCENTS = {"'": "\u0301", "`": "\u0300", "^": "\u0302", '"': "\u0308", "~": "\u0303", "=": "\u0304",
           ".": "\u0307", "u": "\u0306", "v": "\u030c", "H": "\u030b", "c": "\u0327", "k": "\u0328"}


def clean_tex(s: str) -> str:
    s = re.sub(r"\\newblock\s*", " ", s)
    s = re.sub(r"\\penalty-?\d+\s*", "", s)
    s = re.sub(r"\\natexlab\{([^{}]*)\}", r"\1", s)
    s = re.sub(r"\\href\s*\{([^{}]*)\}\s*\{([^{}]*)\}", r"\2 (\1)", s)
    s = re.sub(r"\\(?:url|path|doi)\s*\{([^{}]*)\}", r" \1 ", s)

    def acc(m):
        return unicodedata.normalize("NFC", m.group(2) + ACCENTS[m.group(1)])
    s = re.sub(r"\\([`'^\"~=.])\s*\{?\\?([A-Za-z])\}?", acc, s)
    s = re.sub(r"\\([uvHck])\s*\{\\?([A-Za-z])\}", acc, s)
    for a, b in (("\\ss", "ß"), ("\\o", "ø"), ("\\O", "Ø"), ("\\aa", "å"), ("\\ae", "æ"), ("\\l", "ł")):
        s = re.sub(re.escape(a) + r"(?![A-Za-z])\s?", b, s)
    for _ in range(3):
        s = re.sub(r"\\(?:emph|textit|textbf|textsc|textrm|texttt|textsf|mbox|text|bibinfo\s*\{[^{}]*\}|bibfield\s*\{[^{}]*\})\s*\{([^{}]*)\}", r"\1", s)
        s = re.sub(r"\{\\(?:em|it|bf|sc|tt|rm|sf)\s+([^{}]*)\}", r"\1", s)
    s = s.replace("\\&", "&").replace("\\%", "%").replace("\\_", "_").replace("\\$", "$").replace("~", " ")
    s = s.replace("``", '"').replace("''", '"').replace("---", "—").replace("--", "–")
    s = re.sub(r"\\[A-Za-z@]+\*?", " ", s)
    s = s.replace("{", "").replace("}", "").replace("\\", "")
    return " ".join(s.split())


def find_ids(raw: str) -> dict:
    ids = {}
    m = re.search(r"arxiv(?:\.org/(?:abs|pdf)/|\s*(?:preprint)?\s*[:\s]\s*(?:arxiv:)?|:)\s*(\d{4}\.\d{4,5})(v\d+)?", raw, re.I) \
        or re.search(r"abs/(\d{4}\.\d{4,5})(v\d+)?", raw)
    if m:
        ids["arxiv"] = m.group(1)
    m = re.search(r"\b(10\.\d{4,9}/[^\s,;{}]+)", raw)
    if m:
        ids["doi"] = m.group(1).rstrip(".")
    m = re.search(r"aclanthology\.org/([A-Za-z0-9.\-]*[A-Za-z0-9])", raw) \
        or re.search(r"10\.18653/v1/([A-Za-z0-9.\-]*[A-Za-z0-9])", raw)
    if m:
        acl = re.sub(r"\.pdf$", "", m.group(1))
        if re.search(r"\d", acl):
            ids["acl"] = acl
    m = re.search(r"openreview\.net/(?:forum|pdf)\?id=([A-Za-z0-9_\-]+)", raw)
    if m:
        ids["openreview"] = m.group(1)
    m = re.search(r"https?://[^\s,;{}]+", raw)
    if m:
        ids["url"] = m.group(0).rstrip(".")
    return ids


def parse_bbl(text: str) -> list:
    rx = re.compile(r"\\bibitem\s*(\[(?:[^\[\]]|\[[^\]]*\])*\])?\s*\{([^}]+)\}")
    ms = list(rx.finditer(text))
    out = []
    for i, m in enumerate(ms):
        end = ms[i + 1].start() if i + 1 < len(ms) else text.find("\\end{thebibliography}", m.end())
        chunk = text[m.end(): end if end != -1 else None]
        segs = [clean_tex(x) for x in re.split(r"\\newblock", chunk)]
        raw = clean_tex(chunk)
        label = None
        if m.group(1):
            opt = clean_tex(m.group(1)[1:-1])
            mm = re.match(r"(.+?)\s*\((\d{4}[a-z]?|n\.d\.)\)", opt)
            label = f"{mm.group(1).strip()} ({mm.group(2)})" if mm else opt or None
        year = re.search(r"\b(1[89]\d{2}|20\d{2})[a-z]?\b", raw)
        title = segs[1].rstrip(". ") if len(segs) > 1 and segs[1] else None
        if title:
            title = re.sub(r",\s*(1[89]|20)\d{2}[a-z]?$", "", title).strip() or None
        entry = {"key": m.group(2).strip(), "label": label or f"[{i + 1}]", "n": i + 1, "raw": raw,
                 "title": title, "year": year.group(1) if year else None}
        entry.update(find_ids(chunk))
        out.append(entry)
    return out


def parse_bib(text: str) -> dict:
    entries = {}
    for m in re.finditer(r"@(\w+)\s*\{\s*([^,\s]+)\s*,", text):
        typ = m.group(1).lower()
        if typ in ("comment", "string", "preamble"):
            continue
        start = text.find("{", m.start())
        end = balanced(text, start)
        body = text[m.end(): end if end != -1 else None]
        fields, i = {}, 0
        for fm in re.finditer(r"(\w+)\s*=\s*", body):
            if fm.start() < i:
                continue
            j = fm.end()
            if j < len(body) and body[j] == "{":
                k = balanced(body, j)
                val = body[j + 1:k]
                i = k + 1
            elif j < len(body) and body[j] == '"':
                k = body.find('"', j + 1)
                val = body[j + 1:k]
                i = k + 1
            else:
                mm = re.match(r"[^,}\s]+", body[j:])
                val = mm.group(0) if mm else ""
                i = j + len(val)
            fields[fm.group(1).lower()] = val
        entries[m.group(2).strip()] = {"type": typ, **fields}
    return entries


def bib_to_refs(bib: dict, keys_in_order: list) -> list:
    out = []
    keys = [k for k in keys_in_order if k in bib] or list(bib)
    for n, k in enumerate(keys, 1):
        f = bib[k]
        authors = clean_tex(f.get("author", "")).replace(" and ", ", ")
        title = clean_tex(f.get("title", ""))
        venue = clean_tex(f.get("journal") or f.get("booktitle") or f.get("publisher") or "")
        year = clean_tex(f.get("year", ""))
        raw = ". ".join(x for x in (authors, year, title, venue) if x)
        first = clean_tex(f.get("author", "")).split(" and ")[0]
        surname = first.split(",")[0].strip() if "," in first else (first.split()[-1] if first.split() else k)
        label = f"{surname}{' et al.' if f.get('author', '').count(' and ') >= 2 else ''} ({year})" if year else surname
        e = {"key": k, "label": label, "n": n, "raw": raw, "title": title or None, "year": year or None}
        e.update(find_ids(" ".join(str(v) for v in f.values())))
        if f.get("eprint") and re.match(r"\d{4}\.\d{4,5}", f["eprint"]):
            e["arxiv"] = f["eprint"]
        if f.get("doi"):
            e["doi"] = f["doi"]
        out.append(e)
    return out


CITE_RE = re.compile(r"\\[A-Za-z]*cite[a-zA-Z]*\*?\s*(?:\[[^\]]*\]\s*){0,2}\{([^}]+)\}")


def cited_keys(tex: str) -> list:
    seen, order = set(), []
    for m in CITE_RE.finditer(tex):
        for k in m.group(1).split(","):
            k = k.strip()
            if k and k not in seen:
                seen.add(k)
                order.append(k)
    return order


def find_main_tex(src: Path):
    readme = src / "00README.json"
    if readme.exists():
        try:
            for s in json.loads(readme.read_text()).get("sources", []):
                if s.get("usage") == "toplevel" and (src / s["filename"]).is_file():
                    return src / s["filename"]
        except Exception:  # noqa: BLE001
            pass
    best = None
    for p in src.rglob("*.tex"):
        t = strip_comments(p.read_text(encoding="utf-8", errors="replace"))
        if "\\documentclass" not in t:
            continue
        score = 10 + (10 if "\\begin{document}" in t else 0) + min(len(t) / 10000, 5)
        score += 3 if p.stem.lower() in ("main", "paper", "ms", "manuscript", "article", "root") else 0
        if best is None or score > best[0]:
            best = (score, p)
    return best[1] if best else None


def process_latex(src: Path) -> dict:
    main = find_main_tex(src)
    if main is None:
        raise RuntimeError("no main .tex file (with \\documentclass) found")
    tex = remove_iffalse(flatten(main, main.parent))
    b = tex.find("\\begin{document}")
    preamble, body = (tex[:b], tex[b + len("\\begin{document}"):]) if b != -1 else ("", tex)
    e = body.find("\\end{document}")
    body = body[:e] if e != -1 else body
    macros = collect_macros(preamble + body)
    body = expand_macros(body, macros)
    pre = expand_macros(preamble, macros)

    def grab(cmd, s):
        m = re.search(r"\\" + cmd + r"\s*(?:\[[^\]]*\])?\s*(?=\{)", s)
        if not m:
            return None
        v, _ = arg_after(s, m.end())
        if not v:
            return None
        while True:  # drop \thanks{...} footnotes from titles/author blocks
            t = re.search(r"\\thanks\s*(?=\{)", v)
            if not t:
                break
            _, end = arg_after(v, t.end())
            v = v[:t.start()] + v[end:]
        return clean_tex(v.replace("\\\\", " ").replace("\\And", ",").replace("\\AND", ","))

    title = grab("title", pre) or grab("title", body)
    authors = grab("author", pre) or grab("author", body)
    abstract = None
    am = re.search(r"\\begin\{abstract\}(.*?)\\end\{abstract\}", body, re.S)
    if am:
        abstract = clean_tex(am.group(1))[:3000]
    keys = cited_keys(body)

    # bibliography: .bbl next to main, any .bbl, .bib files, or inline thebibliography
    refs, bib_source = [], None
    inline = re.search(r"\\begin\{thebibliography\}.*?\\end\{thebibliography\}", body, re.S)
    bbls = [main.with_suffix(".bbl")] + sorted(src.rglob("*.bbl"))
    for bbl in bbls:
        if bbl.is_file():
            refs = parse_bbl(bbl.read_text(encoding="utf-8", errors="replace"))
            bib_source = f"bbl:{bbl.name}"
            if refs:
                break
    if not refs and inline:
        refs, bib_source = parse_bbl(inline.group(0)), "inline thebibliography"
    if not refs:
        bib = {}
        for p in sorted(src.rglob("*.bib")):
            bib.update(parse_bib(p.read_text(encoding="utf-8", errors="replace")))
        if bib:
            refs, bib_source = bib_to_refs(bib, keys), "bib"
    if inline:
        body = body.replace(inline.group(0), "\n% (bibliography moved to the References section below)\n")
    md, ntab, nfig = latex_to_markdown(body)
    md = resolve_refs(annotate_floats(md))
    md = re.sub(r"\\bibliography\{[^}]*\}|\\bibliographystyle\{[^}]*\}|\\printbibliography(\[[^\]]*\])?", "", md)
    head = f"# {title}\n" if title else ""
    if authors:
        head += f"\n{authors[:400]}\n"
    md = head + md.strip("\n") + "\n"
    if refs:
        md += "\n\n## References\n\n" + "\n".join(
            f"[{r['key']}] {r['label']}: {r['raw']}" for r in refs) + "\n"
    return {"text": wrap_long_lines(md), "title": title, "authors": authors, "abstract": abstract,
            "references": refs, "bib_source": bib_source, "main_tex": str(main.relative_to(src)),
            "n_tables": ntab, "n_figures": nfig, "n_macros": len(macros), "extractor": "latex"}


# ================================================================ PDF

NUM_HEAD = re.compile(r"^\s*((?:\d{1,2}|[A-H])(?:\.\d{1,2}){0,3})\.?\s+([A-Z][^%=|]{1,90}?)\s*$")
KNOWN_HEADS = {"abstract", "introduction", "related work", "background", "preliminaries", "method", "methods",
               "methodology", "approach", "experiments", "experimental setup", "results", "discussion",
               "analysis", "limitations", "conclusion", "conclusions", "references", "bibliography",
               "acknowledgments", "acknowledgements", "acknowledgment", "appendix", "ethics statement",
               "broader impact", "broader impacts", "impact statement", "future work", "related works",
               "conclusion and future work", "supplementary material"}


def _is_title(t: str, allow_digit: bool = False) -> bool:
    words = t.split()
    if not words or len(words) > 12 or t.endswith((".", ",", ";", ":")):
        return False
    if sum(1 for w in words if re.search(r"\d", w)) >= 2:
        return False
    return t[0].isupper() or (allow_digit and t[0].isdigit())


USUALLY_UNNUMBERED = {"abstract", "references", "bibliography", "acknowledgments", "acknowledgements",
                      "acknowledgment", "appendix", "ethics statement", "broader impact", "broader impacts",
                      "impact statement", "limitations", "supplementary material"}


def mark_headings(lines: list) -> list:
    """Turn PDF heading lines into markdown headings. Numbered headings must follow a plausible
    sequence (1, 2, 2.1, 3 ...; letters only in the back matter), which filters table rows."""
    n = len(lines)
    near_page = {n - 1, n - 2}  # page numbers are footers: the lines just before a page break
    for j, l in enumerate(lines):
        if l.startswith("<!-- page"):
            near_page.update({j - 2, j - 1})
    cands = []  # (index, number, title, lines consumed)
    for i in range(n):
        line = lines[i].strip()
        m = NUM_HEAD.match(line)
        if m and _is_title(m.group(2)) and len(line) < 95:
            cands.append((i, m.group(1), m.group(2), 1))
        elif re.fullmatch(r"(?:\d{1,2}|[A-H])(?:\.\d{1,2}){0,3}\.?", line) and i not in near_page and i + 1 < n:
            nxt = lines[i + 1].strip()
            letter = re.fullmatch(r"[A-H]\.?", line) is not None
            if _is_title(nxt, allow_digit=letter) and len(nxt) < 80 and \
                    (letter or not re.match(r"^\d", nxt)):
                cands.append((i, line.rstrip("."), nxt, 2))
    accepted, last_top, last_letter, back_matter = {}, None, None, False
    for i, num, title, used in cands:
        if any(lines[j].strip().lower() in ("references", "bibliography", "appendix") for j in range(0, i)):
            back_matter = True
        top = num.split(".")[0]
        ok = False
        if top.isdigit():
            t = int(top)
            if "." in num:   # a subsection never runs ahead of its section ("2.5" right after "1 Intro")
                ok = last_letter is None and last_top is not None and t == last_top
            else:
                ok = last_letter is None and (
                    (last_top is None and t in (1, 2)) or (last_top is not None and t in (last_top, last_top + 1)))
            if ok:
                last_top = t
        elif back_matter or (last_top is not None and last_top >= 3):
            ok = (last_letter is None and top == "A") or (last_letter is not None and
                                                          ord(top) in (ord(last_letter), ord(last_letter) + 1))
            if ok:
                last_letter = top
        if ok:
            accepted[i] = (num, title, used)
    numbered = len(accepted) >= 3
    out, i = [], 0
    while i < n:
        if i in accepted:
            num, title, used = accepted[i]
            out.append(f"{'#' * min(num.count('.') + 2, 4)} {num} {title}")
            i += used
            continue
        low = lines[i].strip().lower().rstrip(":")
        if low in KNOWN_HEADS and (not numbered or low in USUALLY_UNNUMBERED):
            out.append(f"## {lines[i].strip()}")
        else:
            out.append(lines[i])
        i += 1
    return out


def hidden_spans(pdf_path) -> list:
    """White, tiny, or off-page text spans (PyMuPDF only)."""
    try:
        try:
            import pymupdf as fitz  # type: ignore
        except ImportError:
            import fitz  # type: ignore
    except ImportError:
        return []
    found = []
    with fitz.open(str(pdf_path)) as doc:
        for pno, page in enumerate(doc, 1):
            rect = page.rect
            # white text is visible when drawn over an image or a coloured shape (heatmap cells,
            # figure labels), so collect those areas first
            backdrops = [fitz.Rect(i["bbox"]) for i in page.get_image_info()]
            for d in page.get_drawings():
                fill = d.get("fill")
                if fill and not all(c > 0.92 for c in fill):
                    backdrops.append(fitz.Rect(d["rect"]))
            buf, reason_buf = [], None
            for block in page.get_text("dict").get("blocks", []):
                for line in block.get("lines", []):
                    for sp in line.get("spans", []):
                        t = sp.get("text", "").strip()
                        if not t:
                            continue
                        c = sp.get("color", 0)
                        r, g, b = (c >> 16) & 255, (c >> 8) & 255, c & 255
                        x0, y0, x1, y1 = sp.get("bbox", (0, 0, 0, 0))
                        reason = None
                        if r > 235 and g > 235 and b > 235:
                            box = fitz.Rect(x0, y0, x1, y1)
                            if not any(box.intersects(bd) for bd in backdrops):
                                reason = "white text"
                        elif sp.get("size", 10) < 2.0:
                            reason = "tiny text"
                        elif x1 < rect.x0 or x0 > rect.x1 or y1 < rect.y0 or y0 > rect.y1:
                            reason = "off-page text"
                        if reason:
                            buf.append(t)
                            reason_buf = reason
                        elif buf:
                            found.append({"page": pno, "reason": reason_buf, "text": " ".join(buf)})
                            buf = []
            if buf:
                found.append({"page": pno, "reason": reason_buf, "text": " ".join(buf)})
    return [f for f in found if len(f["text"]) >= 12]


def _ref_region(lines: list):
    start = None
    for i, l in enumerate(lines):
        if re.fullmatch(r"#*\s*(references|bibliography|literature cited)\s*", l.strip(), re.I):
            start = i + 1
    if start is None:
        return None, None
    end = len(lines)
    for j in range(start, len(lines)):
        s = lines[j].strip()
        if re.match(r"^#+\s+(A|Appendix|Supplementary|NeurIPS Paper Checklist|Checklist)\b", s) or \
           re.match(r"^#+\s+A(\.\d+)?\s", s):
            end = j
            break
    return start, end


def _entries_from_lines(lines: list) -> list:
    body = [l.rstrip() for l in lines if not l.startswith("<!-- page")]
    text_lines = [l for l in body]
    numeric = [i for i, l in enumerate(text_lines) if re.match(r"^\s*\[\d{1,3}\]\s", l)]
    entries = []
    if len(numeric) >= 3:
        for a, b in zip(numeric, numeric[1:] + [len(text_lines)]):
            entries.append(text_lines[a:b])
        return entries
    if sum(1 for l in text_lines if not l.strip()) >= 3:
        cur = []
        for l in text_lines:
            if not l.strip():
                if cur:
                    entries.append(cur)
                cur = []
            else:
                cur.append(l)
        if cur:
            entries.append(cur)
        if len(entries) >= 3:
            return entries
    # heuristic: a new entry starts with a name-like token after a line that ended an entry
    entries, cur = [], []
    name_start = re.compile(r"^[A-Z][A-Za-zÀ-ÿ'’\-]+(?:,\s*[A-Z]|\s+[A-Z][A-Za-zÀ-ÿ'’\-]+(?:,|\s+and\b|\s+[A-Z]\.))")
    for l in text_lines:
        s = l.strip()
        if not s:
            continue
        prev_end = cur[-1].rstrip() if cur else ""
        if cur and name_start.match(s) and re.search(r"(\.|\d{4}[a-z]?\.?|\))$", prev_end) and \
                re.search(r"\b(19|20)\d{2}", " ".join(cur)):
            entries.append(cur)
            cur = []
        cur.append(s)
    if cur:
        entries.append(cur)
    return entries


def _entries_from_layout(pdf_path) -> list:
    """Reference entries via hanging indents / vertical gaps (PyMuPDF)."""
    try:
        try:
            import pymupdf as fitz  # type: ignore
        except ImportError:
            import fitz  # type: ignore
    except ImportError:
        return []
    rows, sizes, active, done = [], [], False, False
    with fitz.open(str(pdf_path)) as doc:
        for page in doc:
            if done:
                break
            mid = page.rect.width / 2
            for block in page.get_text("dict").get("blocks", []):
                # merge fragments that sit on the same baseline inside one block (justified text
                # with wide gaps comes out as several "lines")
                visual = []
                for line in block.get("lines", []):
                    spans = line.get("spans", [])
                    t = "".join(sp.get("text", "") for sp in spans).strip()
                    if not t:
                        continue
                    x0, y0, x1, y1 = line["bbox"]
                    size = max((sp.get("size", 0) for sp in spans), default=0)
                    bold = any((sp.get("flags", 0) & 16) or "bold" in sp.get("font", "").lower() for sp in spans)
                    if visual and abs(visual[-1]["y0"] - y0) < 2.0 and x0 >= visual[-1]["x1"] - 1:
                        v = visual[-1]
                        v.update(t=v["t"] + " " + t, x1=x1, size=max(v["size"], size), bold=v["bold"] or bold)
                    else:
                        visual.append({"t": t, "x0": x0, "y0": y0, "x1": x1, "y1": y1, "size": size, "bold": bold})
                for v in visual:
                    t = v["t"]
                    if done:
                        break
                    if re.fullmatch(r"\d{1,4}", t):  # page numbers
                        continue
                    if re.fullmatch(r"(\d+\s+)?(references|bibliography)", t, re.I):
                        active, rows, sizes = True, [], []
                        continue
                    if active:
                        med = sorted(sizes)[len(sizes) // 2] if sizes else v["size"]
                        heading_like = v["bold"] or v["size"] > 1.12 * med
                        if heading_like and len(t) < 70 and (
                                re.match(r"^(A|Appendix|[A-H]\.?\d*)\s+[A-Z0-9]", t) or
                                re.match(r"^(NeurIPS Paper Checklist|Checklist|Supplementary|Appendix)", t)):
                            done = True
                            continue
                        rows.append((page.number, 0 if v["x0"] < mid else 1, v["x0"], v["y0"], v["y1"], t))
                        sizes.append(v["size"])
    if len(rows) < 6:
        return []
    from collections import defaultdict
    margin = defaultdict(lambda: 1e9)
    for p, col, x0, *_ in rows:
        margin[(p, col)] = min(margin[(p, col)], x0)
    indents = [x0 - margin[(p, col)] for p, col, x0, *_ in rows]
    frac = sum(1 for d in indents if d >= 4) / len(indents)
    gaps = sorted(b[3] - a[4] for a, b in zip(rows, rows[1:]) if a[0] == b[0] and a[1] == b[1] and b[3] > a[4] - 1)
    med = gaps[len(gaps) // 2] if gaps else 2.0
    entries, cur, prev = [], [], None
    for row, d in zip(rows, indents):
        p, col, x0, y0, y1, t = row
        new = False
        if 0.15 <= frac <= 0.9:
            new = d < 2
        elif prev is not None and prev[0] == p and prev[1] == col:
            new = (y0 - prev[4]) > max(1.3 * med, med + 2)
        else:
            new = prev is None
        if new and cur:
            entries.append(cur)
            cur = []
        cur.append(t)
        prev = row
    if cur:
        entries.append(cur)
    return entries


def _join_entry(parts: list) -> str:
    s = ""
    for p in parts:
        p = p.strip()
        if s.endswith("-") and p[:1].islower() and len(s) > 1 and s[-2].isalpha():
            s = s[:-1] + p          # undo end-of-line hyphenation: "num-" + "ber"
        else:
            s = (s + " " + p) if s else p
    return " ".join(s.split())


def first_author_surname(body: str) -> str:
    """'AR. Ahmed and B. Singh. Title…' → Ahmed; 'Ahmed, A. and …' → Ahmed; 'Jae Lee, Min Kim…' → Lee."""
    m = re.search(r"(?<=[a-zà-ÿ]{2})\.\s", body)     # first period that ends a real word, not an initial
    block = body[: m.start()] if m else body[:120]
    first = re.split(r",|;|\s+and\s+|\s+&\s+", block)[0]
    words = [w for w in re.findall(r"[A-Za-zÀ-ÿ'’\-]+", first) if len(w) > 1 and any(c.islower() for c in w)]
    return words[-1] if words else "ref"


def refs_from_entries(entries: list) -> list:
    out, used = [], {}
    for n, parts in enumerate(entries, 1):
        raw = _join_entry(parts)
        if len(raw) < 15:
            continue
        mnum = re.match(r"^\[(\d{1,3})\]\s*(.*)$", raw)
        body = mnum.group(2) if mnum else raw
        year = re.search(r"\b(1[89]\d{2}|20\d{2})[a-z]?\b", body)
        surname = first_author_surname(body)
        segs = [s.strip() for s in re.split(r"(?<=[a-z0-9\)])\.\s+(?=[A-Z0-9\"“])", body) if s.strip()]
        title = None
        for s in segs[1:4]:
            if re.fullmatch(r"(19|20)\d{2}[a-z]?\.?", s):
                continue
            if len(s.split()) >= 3:
                title = s.rstrip(".")
                break
        if mnum:
            key, label = f"ref{mnum.group(1)}", f"[{mnum.group(1)}]"
        else:
            base = unicodedata.normalize("NFKD", surname).encode("ascii", "ignore").decode().lower() + \
                (year.group(1) if year else "")
            used[base] = used.get(base, 0) + 1
            key = base + ("" if used[base] == 1 else chr(ord("a") + used[base] - 1))
            label = f"{surname} ({year.group(1)})" if year else surname
        e = {"key": key, "label": label, "n": n, "raw": raw, "title": title, "year": year.group(1) if year else None}
        e.update(find_ids(raw))
        out.append(e)
    return out


def strip_margin_numbers(lines: list) -> list:
    """Drop review-format margin line numbers: runs of 5+ consecutive 3- or 4-digit numbers (000, 001, ... 1000, 1001)."""
    out, i, n = [], 0, len(lines)
    num = re.compile(r"\d{3,4}")
    while i < n:
        if num.fullmatch(lines[i].strip()):
            j = i
            while j + 1 < n and num.fullmatch(lines[j + 1].strip()) and \
                    int(lines[j + 1].strip()) == int(lines[j].strip()) + 1:
                j += 1
            if j - i + 1 >= 5:
                i = j + 1
                continue
        out.append(lines[i])
        i += 1
    return out


def process_pdf(pdf: Path) -> dict:
    pages, extractor = pdf_pages(pdf)
    lines = []
    for i, ptxt in enumerate(pages, 1):
        lines.append(f"<!-- page {i} -->")
        lines.extend(strip_margin_numbers(ptxt.replace("\r", "").split("\n")))
    lines = mark_headings(lines)
    a, b = _ref_region(lines)
    region = lines[a:b] if a is not None else []
    numeric = sum(1 for l in region if re.match(r"^\s*\[\d{1,3}\]", l)) >= 3
    entries = []
    if extractor == "pymupdf" and not numeric:   # author-year lists: hanging indents / gaps
        entries = _entries_from_layout(pdf)
    if len(entries) < 3 and region:              # numeric lists, or no layout information
        entries = _entries_from_lines(region)
    refs = refs_from_entries(entries)
    title = None
    for l in lines[1:30]:
        s = l.strip()
        if len(s) > 12 and not s.startswith("<!--") and not re.search(
                r"arxiv|preprint|@|\d{4}\.\d{4}|under review|conference paper|double-blind|anonymous author|"
                r"published as|proceedings of|workshop", s, re.I):
            title = s
            break
    text = "\n".join(lines)
    text = re.sub(r"\n{4,}", "\n\n\n", text)
    return {"text": text + "\n", "title": title, "authors": None, "abstract": None, "references": refs,
            "bib_source": f"pdf-{'text' if numeric or extractor != 'pymupdf' else 'layout'}",
            "n_pages": len(pages), "extractor": extractor, "hidden": hidden_spans(pdf)}


def process_text(path: Path) -> dict:
    text = path.read_text(encoding="utf-8", errors="replace")
    lines = text.split("\n")
    if not any(l.startswith("#") for l in lines):
        lines = mark_headings(lines)
    a, b = _ref_region(lines)
    refs = refs_from_entries(_entries_from_lines(lines[a:b])) if a is not None else []
    title = next((l.lstrip("# ").strip() for l in lines if l.strip()), None)
    return {"text": "\n".join(lines).rstrip("\n") + "\n", "title": title, "authors": None, "abstract": None,
            "references": refs, "bib_source": "text", "extractor": "text"}


# ================================================================ integrity scan

INJECTION_PATTERNS = [
    (r"ignore\s+(?:all\s+|any\s+)?(?:the\s+)?(?:previous|prior|above|earlier|preceding)\s+(?:instructions|prompts|directions)", "instruction override"),
    (r"disregard\s+(?:all\s+|any\s+)?(?:the\s+)?(?:previous|prior|above)\s+(?:instructions|prompts)", "instruction override"),
    (r"\b(?:llm|ai|language[- ]model|automated)\s+reviewers?\b", "addresses AI reviewers"),
    (r"\bgive\s+(?:this\s+paper\s+)?(?:a\s+)?(?:positive|favou?rable|high|glowing)\s+(?:review|rating|score|assessment)", "requests favourable review"),
    (r"\b(?:rate|score|mark|treat|label)\s+(?:this\s+paper|every\s+claim|all\s+(?:the\s+)?claims)\b", "requests a verdict"),
    (r"\bdo\s+not\s+(?:mention|highlight|point\s+out|report)\s+(?:any\s+)?(?:weakness|negative|limitation|flaw)", "suppresses criticism"),
    (r"\bas\s+an?\s+(?:ai|llm|language\s+model|assistant)\b", "addresses an AI"),
    (r"<\|[a-z_]+\|>|\[/?INST\]|<<SYS>>", "chat-template token"),
    (r"\\color\{white\}|\\textcolor\{white\}|\\color\[rgb\]\{1,\s*1,\s*1\}|\\fontsize\{0", "hidden-text formatting"),
]


def integrity_scan(text: str, hidden: list) -> list:
    idx = TextIndex(text)
    findings = []
    for pat, why in INJECTION_PATTERNS:
        rx = re.compile(pat, re.I)
        for i, line in enumerate(idx.lines, 1):
            if rx.search(line):
                findings.append({"line": i, "reason": why, "text": line.strip()[:300]})
    for h in hidden:
        hit = idx.find(h["text"][:200])
        findings.append({"line": hit.get("line"), "page": h["page"], "reason": h["reason"], "text": h["text"][:300]})
    merged = {}
    for f in sorted(findings, key=lambda f: (f.get("line") or 0)):
        k = f.get("line") if f.get("line") is not None else f"p{f.get('page')}:{f['text'][:40]}"
        if k in merged:
            if f["reason"] not in merged[k]["reason"]:
                merged[k]["reason"] += "; " + f["reason"]
        else:
            merged[k] = dict(f)
    return list(merged.values())


def sections_of(text: str) -> list:
    secs, lines = [], text.split("\n")
    for i, l in enumerate(lines, 1):
        m = re.match(r"^(#{1,4})\s+(.+)$", l)
        if m:
            secs.append({"title": m.group(2).strip()[:100], "level": len(m.group(1)), "start": i})
    for j, s in enumerate(secs):
        s["end"] = (secs[j + 1]["start"] - 1) if j + 1 < len(secs) else len(lines)
    return secs


LATEX_SUFFIXES = {".tex", ".bbl", ".bib", ".sty", ".cls", ".bst", ".json"}
SKIP_DIRS = {".git", "node_modules", "pvd-audit", "__pycache__", ".venv", "venv"}


def copy_latex_tree(src: Path, dest: Path, max_files: int = 2000, max_depth: int = 5):
    """Copy only LaTeX-relevant files (no PDFs/images/data) so a big project folder stays cheap."""
    if dest.exists():
        shutil.rmtree(dest)
    dest.mkdir(parents=True)
    n = 0
    for f in src.rglob("*"):
        rel = f.relative_to(src)
        if len(rel.parts) > max_depth or any(part in SKIP_DIRS for part in rel.parts):
            continue
        if f.is_file() and f.suffix.lower() in LATEX_SUFFIXES and f.stat().st_size < 20_000_000:
            (dest / rel).parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(f, dest / rel)
            n += 1
            if n >= max_files:
                break
    return n


def unpack_source(data: bytes, raw: Path) -> str:
    """Unpack an arXiv e-print or local source archive into raw/src. Returns 'latex' or 'pdf'."""
    srcdir = raw / "src"
    if srcdir.exists():
        shutil.rmtree(srcdir)
    if data[:2] == b"\x1f\x8b" and not tarfile.is_tarfile(io.BytesIO(data)):
        data = gzip.decompress(data)          # a single gzipped .tex file
    if data[:4] == b"%PDF":
        (raw / "paper.pdf").write_bytes(data)
        return "pdf"
    try:
        safe_extract(data, srcdir)
    except (tarfile.ReadError, OSError, RuntimeError, EOFError):
        srcdir.mkdir(parents=True, exist_ok=True)
        (srcdir / "main.tex").write_bytes(data)
    return "latex"


# ================================================================ main


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("source")
    ap.add_argument("--out", help="audit folder (default ./pvd-audit/<slug>)")
    ap.add_argument("--prefer", choices=["latex", "pdf"], default="latex")
    ap.add_argument("--with-pdf", action="store_true", help="for arXiv LaTeX ingests, also fetch the PDF (hidden-text scan)")
    ap.add_argument("--force", action="store_true", help="overwrite an existing paper/ folder")
    ns = ap.parse_args()

    src = ns.source
    aid = None if Path(src).exists() else arxiv_id_from(src)
    slug = slugify(aid or Path(src.rstrip("/")).stem or src)
    audit = Path(ns.out) if ns.out else Path("pvd-audit") / slug
    paper_dir = audit / "paper"
    if (paper_dir / "paper.md").exists() and not ns.force:
        sys.exit(f"{paper_dir}/paper.md already exists (use --force to re-ingest)")
    raw = paper_dir / "raw"
    raw.mkdir(parents=True, exist_ok=True)
    warnings, result, meta_extra = [], None, {}

    def from_pdf(pdf_path):
        return process_pdf(pdf_path)

    def from_latex_dir(d):
        return process_latex(d)

    if aid:
        meta_extra["arxiv_id"] = aid
        try:
            meta_extra.update({k: v for k, v in arxiv_metadata(aid).items() if v})
        except Exception as e:  # noqa: BLE001
            warnings.append(f"arXiv metadata unavailable: {e}")
        if ns.prefer == "latex":
            try:
                data = http_get(f"https://arxiv.org/e-print/{aid}")
                (raw / "eprint.bin").write_bytes(data)
                kind = unpack_source(data, raw)
                if kind == "pdf":
                    result = from_pdf(raw / "paper.pdf")
                    warnings.append("arXiv has no LaTeX source for this paper; used the PDF")
                else:
                    result = from_latex_dir(raw / "src")
            except Exception as e:  # noqa: BLE001
                warnings.append(f"LaTeX source path failed ({e}); falling back to PDF")
        if result is None or ns.with_pdf:
            try:
                pdf = raw / "paper.pdf"
                if not pdf.exists():
                    pdf.write_bytes(http_get(f"https://arxiv.org/pdf/{aid}"))
                if result is None:
                    result = from_pdf(pdf)
                else:
                    result["hidden"] = hidden_spans(pdf)
            except Exception as e:  # noqa: BLE001
                if result is None:
                    sys.exit(f"could not fetch arXiv {aid}: {e}\nDownload the PDF or source yourself and pass the local path.")
                warnings.append(f"PDF fetch failed: {e}")
    elif re.match(r"^https?://", src):
        data = http_get(src)
        if data[:4] != b"%PDF":
            sys.exit("URL did not return a PDF; download the paper and pass a local path")
        (raw / "paper.pdf").write_bytes(data)
        result = from_pdf(raw / "paper.pdf")
        meta_extra["url"] = src
    else:
        p = Path(src)
        if not p.exists():
            sys.exit(f"no such file or arXiv id: {src}")
        if p.is_dir() or p.suffix.lower() == ".tex":
            root = p if p.is_dir() else p.parent
            copy_latex_tree(root, raw / "src")
            result = from_latex_dir(raw / "src")
            main_tex = result.get("main_tex") or ""
            pdf = root / Path(main_tex).with_suffix(".pdf") if main_tex else None
            if pdf and pdf.is_file():  # compiled PDF next to the source: figures + hidden-text scan
                shutil.copy2(pdf, raw / "paper.pdf")
                result["hidden"] = hidden_spans(raw / "paper.pdf")
            else:
                warnings.append("no compiled PDF next to the main .tex — hidden-text scan and figure "
                                "rendering are unavailable (put main.pdf beside main.tex to enable them)")
        elif p.suffix.lower() == ".pdf":
            shutil.copy2(p, raw / "paper.pdf")
            result = from_pdf(raw / "paper.pdf")
        elif p.suffix.lower() in (".gz", ".tgz", ".tar", ".zip") or p.name.endswith(".tar.gz"):
            kind = unpack_source(p.read_bytes(), raw)
            result = from_pdf(raw / "paper.pdf") if kind == "pdf" else from_latex_dir(raw / "src")
        else:
            shutil.copy2(p, raw / p.name)
            result = process_text(p)

    text = result["text"]
    write_text(paper_dir / "paper.md", text)
    write_json(paper_dir / "references.json", result.get("references", []))
    idx = TextIndex(text)
    secs = sections_of(text)
    integrity = integrity_scan(text, result.get("hidden", []))
    meta = {
        "title": meta_extra.get("title") or result.get("title"),
        "authors": meta_extra.get("authors") or result.get("authors"),
        "abstract": meta_extra.get("abstract") or result.get("abstract"),
        "source": src, "source_type": result.get("extractor"), "bib_source": result.get("bib_source"),
        "n_lines": len(idx), "n_pages": result.get("n_pages"), "n_references": len(result.get("references", [])),
        "n_tables": result.get("n_tables"), "main_tex": result.get("main_tex"),
        "sections": secs, "integrity": integrity, "warnings": warnings, "ingested": now_iso(),
    }
    for k in ("arxiv_id", "url", "published"):
        if k in meta_extra:
            meta[k] = meta_extra[k]
    write_json(paper_dir / "meta.json", meta)

    print(f"Ingested: {meta['title'] or '(untitled)'}")
    print(f"  audit folder: {audit.resolve()}")
    print(f"  extractor: {meta['source_type']} | paper.md: {meta['n_lines']} lines | "
          f"{len(secs)} headings | {meta['n_references']} references ({meta['bib_source']})")
    top = [s for s in secs if s["level"] <= 2][:14]
    if top:
        print("  outline: " + " · ".join(f"{s['title'][:28]} L{s['start']}" for s in top))
    if integrity:
        print(f"  INTEGRITY: {len(integrity)} passage(s) look like instructions to AI readers or hidden text:")
        for f in integrity[:5]:
            print(f"    L{f.get('line')}: {f['reason']} — {f['text'][:100]}")
    for w in warnings:
        print(f"  warning: {w}")
    if meta["n_references"] == 0:
        print("  warning: no references parsed; attribution checks will rely on the paper's reference section text")


if __name__ == "__main__":
    main()
