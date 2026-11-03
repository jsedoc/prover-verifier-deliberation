#!/usr/bin/env python3
"""Reference lookup and cited-source caching (prover-verifier-paper skill).

  refs.py lookup --audit DIR "Lee 2021"
        find reference keys by author / year / title words
  refs.py add --audit DIR --key KEY (--file PATH | --url URL | --text "...") [--title T] [--coverage C]
        register a cited source you already have (PDF, .md/.txt, HTML page, or pasted text).
        Use keys from references.json; for sources the paper does not cite, use ext-<name>.
  refs.py fetch --audit DIR [--keys k1,k2 | --from-claims] [--max 60] [--no-s2]
        download open-access text for cited works: arXiv, ACL Anthology, OpenReview, direct PDF
        links, then Semantic Scholar (title/DOI match, open-access PDF or abstract)
  refs.py status --audit DIR

Cached sources live in DIR/sources/<key>.md (line-anchored like paper.md) and are indexed in
DIR/sources/index.json with coverage full-text | abstract-only | excerpt | unavailable.
Set S2_API_KEY to raise Semantic Scholar rate limits.
"""
from __future__ import annotations

import argparse
import html
import json
import os
import re
import sys
import time
import urllib.parse
from html.parser import HTMLParser
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _common import BLOCKED_HOSTS, NetworkBlocked, http_get, loose, pdf_pages, read_json, today, write_json, write_text  # noqa: E402,E501

S2 = "https://api.semanticscholar.org/graph/v1"
S2_FIELDS = "title,abstract,year,externalIds,openAccessPdf"
MIN_TEXT = 200  # characters; less means a scanned PDF without a text layer


def load_refs(audit: Path) -> list:
    return read_json(audit / "paper" / "references.json", default=[])


def load_index(audit: Path) -> dict:
    return read_json(audit / "sources" / "index.json", default={})


def save_source(audit: Path, key: str, text: str, *, label: str, title: str, coverage: str, origin: str):
    src = audit / "sources"
    src.mkdir(parents=True, exist_ok=True)
    header = (f"<!-- source key={key} | label={label or key} | coverage={coverage} | origin={origin} | "
              f"retrieved={today()} -->\n")
    body = text.strip("\n")
    if title and not body.lstrip().startswith("#"):
        body = f"# {title}\n\n{body}"
    write_text(src / f"{key}.md", header + body + "\n")
    idx = load_index(audit)
    idx[key] = {"coverage": coverage, "origin": origin, "title": title, "label": label,
                "file": f"sources/{key}.md", "retrieved": today(),
                "lines": body.count("\n") + 2}
    write_json(src / "index.json", idx)
    return idx[key]


class _Text(HTMLParser):
    def __init__(self):
        super().__init__()
        self.out, self.skip = [], 0

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style", "nav", "header", "footer", "noscript"):
            self.skip += 1
        if tag in ("p", "div", "br", "li", "h1", "h2", "h3", "h4", "tr", "section"):
            self.out.append("\n")

    def handle_endtag(self, tag):
        if tag in ("script", "style", "nav", "header", "footer", "noscript") and self.skip:
            self.skip -= 1

    def handle_data(self, data):
        if not self.skip:
            self.out.append(data)


def html_to_text(raw: bytes) -> str:
    p = _Text()
    p.feed(raw.decode("utf-8", "replace"))
    text = html.unescape("".join(p.out))
    lines = [" ".join(l.split()) for l in text.split("\n")]
    return re.sub(r"\n{3,}", "\n\n", "\n".join(l for l in lines)).strip()


def pdf_bytes_to_text(data: bytes, audit: Path, key: str) -> str:
    tmp = audit / "sources" / f".{key}.tmp.pdf"
    tmp.parent.mkdir(parents=True, exist_ok=True)
    tmp.write_bytes(data)
    try:
        pages, _ = pdf_pages(tmp)
    finally:
        tmp.unlink(missing_ok=True)
    return "\n".join(f"<!-- page {i} -->\n{p}" for i, p in enumerate(pages, 1))


# ---------------------------------------------------------------- commands


def cmd_lookup(ns):
    audit = Path(ns.audit)
    refs = load_refs(audit)
    toks = [t for t in re.findall(r"[a-z0-9]+", ns.query.lower()) if len(t) > 1]
    scored = []
    for r in refs:
        hay = " ".join(str(r.get(k) or "") for k in ("key", "label", "title", "raw")).lower()
        hay_l = loose(hay)
        s = sum(2 if t in (r.get("key") or "").lower() else 1 for t in toks if t in hay or loose(t) in hay_l)
        if s:
            scored.append((s, r))
    scored.sort(key=lambda x: -x[0])
    if not scored:
        print("no match; read the References section of paper/paper.md")
        return
    idx = load_index(audit)
    for s, r in scored[:6]:
        cov = idx.get(r["key"], {}).get("coverage", "not cached")
        print(f"{r['key']:<24} {r.get('label') or '':<28} {(r.get('title') or r.get('raw', ''))[:80]}  [{cov}]")


def cmd_add(ns):
    audit = Path(ns.audit)
    refs = {r["key"]: r for r in load_refs(audit)}
    ref = refs.get(ns.key, {})
    if not ref and not ns.key.startswith("ext-"):
        print(f"note: {ns.key} is not in references.json; non-cited sources should use an ext- prefix", file=sys.stderr)
    if ns.file:
        p = Path(ns.file)
        if p.suffix.lower() == ".pdf":
            text = pdf_bytes_to_text(p.read_bytes(), audit, ns.key)
        elif p.suffix.lower() in (".html", ".htm"):
            text = html_to_text(p.read_bytes())
        else:
            text = p.read_text(encoding="utf-8", errors="replace")
        origin = f"file:{p.name}"
    elif ns.url:
        data = http_get(ns.url)
        text = pdf_bytes_to_text(data, audit, ns.key) if data[:4] == b"%PDF" else html_to_text(data)
        origin = ns.url
    elif ns.text:
        text, origin = ns.text, "pasted"
    else:
        sys.exit("give --file, --url or --text")
    coverage = ns.coverage or ("excerpt" if ns.text else "full-text")
    info = save_source(audit, ns.key, text, label=ref.get("label") or ns.key,
                       title=ns.title or ref.get("title") or "", coverage=coverage, origin=origin)
    print(f"saved sources/{ns.key}.md ({info['lines']} lines, {coverage})")


def _s2_get(path: str):
    headers_key = os.environ.get("S2_API_KEY")
    url = f"{S2}{path}"
    if headers_key:
        import urllib.request
        req = urllib.request.Request(url, headers={"x-api-key": headers_key, "User-Agent": "prover-verifier-paper"})
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.loads(r.read())
    return json.loads(http_get(url, timeout=30))


def _similar(a: str, b: str) -> float:
    from difflib import SequenceMatcher
    return SequenceMatcher(None, loose(a or ""), loose(b or "")).ratio()


def why(e: BaseException) -> str:
    return "blocked by the network" if isinstance(e, NetworkBlocked) else type(e).__name__


def fetch_one(audit: Path, ref: dict, use_s2: bool) -> dict:
    key, tried = ref["key"], []

    def pdf(url, origin):
        tried.append(origin)
        data = http_get(url, timeout=60)
        if data[:4] != b"%PDF":
            raise RuntimeError("not a PDF")
        return pdf_bytes_to_text(data, audit, key)

    title = ref.get("title") or ""
    label = ref.get("label") or key
    candidates = []
    if ref.get("arxiv"):
        candidates.append((f"https://arxiv.org/pdf/{ref['arxiv']}", f"arxiv:{ref['arxiv']}"))
    if ref.get("acl"):
        candidates.append((f"https://aclanthology.org/{ref['acl']}.pdf", f"acl:{ref['acl']}"))
    if ref.get("openreview"):
        candidates.append((f"https://openreview.net/pdf?id={ref['openreview']}", f"openreview:{ref['openreview']}"))
    if ref.get("url") and re.search(r"\.pdf($|\?)", ref["url"]):
        candidates.append((ref["url"], ref["url"]))
    for url, origin in candidates:
        try:
            text = pdf(url, origin)
            if len(text) > MIN_TEXT:
                return save_source(audit, key, text, label=label, title=title, coverage="full-text", origin=origin)
            tried[-1] += " (no text layer)"
        except Exception as e:  # noqa: BLE001
            tried[-1] += f" ({why(e)})"
    if use_s2:
        try:
            if ref.get("doi"):
                paper = _s2_get(f"/paper/DOI:{urllib.parse.quote(ref['doi'])}?fields={S2_FIELDS}")
            elif ref.get("arxiv"):
                paper = _s2_get(f"/paper/arXiv:{ref['arxiv']}?fields={S2_FIELDS}")
            elif title:
                res = _s2_get(f"/paper/search/match?query={urllib.parse.quote(title)}&fields={S2_FIELDS}")
                paper = (res.get("data") or [None])[0]
                if paper and _similar(paper.get("title"), title) < 0.8:
                    tried.append(f"s2: best match '{paper.get('title')}' rejected (title mismatch)")
                    paper = None
            else:
                paper = None
            time.sleep(1.1)
            if paper:
                tried.append(f"s2:{paper.get('paperId', '?')}")
                ext = paper.get("externalIds") or {}
                oa = (paper.get("openAccessPdf") or {}).get("url")
                for url, origin in ([(f"https://arxiv.org/pdf/{ext['ArXiv']}", f"arxiv:{ext['ArXiv']}")] if ext.get("ArXiv") and not ref.get("arxiv") else []) + \
                                   ([(oa, oa)] if oa else []):
                    try:
                        text = pdf(url, origin)
                        if len(text) > MIN_TEXT:
                            return save_source(audit, key, text, label=label, title=paper.get("title") or title,
                                               coverage="full-text", origin=origin)
                    except Exception as e:  # noqa: BLE001
                        tried[-1] += f" ({why(e)})"
                if paper.get("abstract"):
                    return save_source(audit, key, "## Abstract\n" + paper["abstract"], label=label,
                                       title=paper.get("title") or title, coverage="abstract-only",
                                       origin=f"semanticscholar:{paper.get('paperId')}")
        except Exception as e:  # noqa: BLE001
            tried.append(f"semanticscholar ({why(e)})" if isinstance(e, NetworkBlocked)
                         else f"s2 error ({type(e).__name__}: {str(e)[:80]})")
    idx = load_index(audit)
    idx[key] = {"coverage": "unavailable", "title": title, "label": label, "tried": tried, "retrieved": today()}
    write_json(audit / "sources" / "index.json", idx)
    return idx[key]


def cmd_fetch(ns):
    audit = Path(ns.audit)
    refs = {r["key"]: r for r in load_refs(audit)}
    if ns.keys:
        keys = [k.strip() for k in ns.keys.split(",") if k.strip()]
    elif ns.from_claims:
        data = read_json(audit / "claims.json", default={"claims": []})
        claims = data["claims"] if isinstance(data, dict) else data
        first = [k for c in claims if c.get("type") == "attribution" for k in c.get("cites", [])]
        rest = [k for c in claims for k in c.get("cites", [])]
        keys = list(dict.fromkeys(first + rest))
    else:
        keys = list(refs)
    idx = load_index(audit)
    todo = [k for k in keys if k in refs and idx.get(k, {}).get("coverage") not in ("full-text", "excerpt")]
    missing = [k for k in keys if k not in refs]
    if missing:
        print(f"not in references.json (skipped): {', '.join(missing[:20])}")
    todo = todo[: ns.max]
    print(f"fetching {len(todo)} source(s)…")
    for k in todo:
        info = fetch_one(audit, refs[k], use_s2=not ns.no_s2)
        print(f"  {k:<24} {info['coverage']:<14} {info.get('origin') or '; '.join(info.get('tried', []))[:90]}",
              flush=True)
    if BLOCKED_HOSTS:
        print(f"\nthe network refused connections to {', '.join(sorted(BLOCKED_HOSTS))}: this environment's network "
              f"policy blocks them. Sources from those sites must be supplied as files: "
              f"refs.py add --audit {ns.audit} --key KEY --file PAPER.pdf")
    cmd_status(ns)


def cmd_status(ns):
    audit = Path(ns.audit)
    idx = load_index(audit)
    refs = load_refs(audit)
    counts = {}
    for v in idx.values():
        counts[v["coverage"]] = counts.get(v["coverage"], 0) + 1
    print(f"sources: {len(idx)} indexed of {len(refs)} references — " +
          ", ".join(f"{n} {c}" for c, n in sorted(counts.items())) if idx else
          f"sources: none cached yet ({len(refs)} references)")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("lookup"); p.add_argument("--audit", required=True); p.add_argument("query")
    p.set_defaults(fn=cmd_lookup)
    p = sub.add_parser("add"); p.add_argument("--audit", required=True); p.add_argument("--key", required=True)
    p.add_argument("--file"); p.add_argument("--url"); p.add_argument("--text"); p.add_argument("--title")
    p.add_argument("--coverage", choices=["full-text", "abstract-only", "excerpt"])
    p.set_defaults(fn=cmd_add)
    p = sub.add_parser("fetch"); p.add_argument("--audit", required=True); p.add_argument("--keys")
    p.add_argument("--from-claims", action="store_true"); p.add_argument("--max", type=int, default=60)
    p.add_argument("--no-s2", action="store_true"); p.set_defaults(fn=cmd_fetch)
    p = sub.add_parser("status"); p.add_argument("--audit", required=True); p.set_defaults(fn=cmd_status)
    ns = ap.parse_args()
    ns.fn(ns)


if __name__ == "__main__":
    main()
