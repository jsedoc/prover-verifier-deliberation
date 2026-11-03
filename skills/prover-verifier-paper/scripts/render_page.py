#!/usr/bin/env python3
"""Render a page of the paper's PDF to PNG so an agent can look at a figure (prover-verifier-paper).

  render_page.py --audit DIR --page N [--dpi 110]
  render_page.py --audit DIR --find "Figure 3"      render the first page whose text contains the phrase

Writes DIR/figures/page-N.png and prints the path; open it with the Read tool to view it.
Needs PyMuPDF (pip install pymupdf) and DIR/paper/raw/paper.pdf (ingest a PDF, use --with-pdf for
arXiv LaTeX ingests, or keep the compiled main.pdf next to a local main.tex).
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _common import loose  # noqa: E402


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--audit", required=True)
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--page", type=int)
    g.add_argument("--find")
    ap.add_argument("--dpi", type=int, default=110)
    ns = ap.parse_args()
    audit = Path(ns.audit)
    pdf = audit / "paper" / "raw" / "paper.pdf"
    if not pdf.exists():
        sys.exit(f"no PDF at {pdf} — re-ingest from a PDF (or with --with-pdf) to view figures")
    try:
        import pymupdf as fitz  # type: ignore
    except ImportError:
        try:
            import fitz  # type: ignore
        except ImportError:
            sys.exit("PyMuPDF is not installed: pip install pymupdf")
    with fitz.open(str(pdf)) as doc:
        if ns.find:
            want = loose(ns.find)
            hits = [i + 1 for i, p in enumerate(doc) if want in loose(p.get_text("text"))]
            if not hits:
                sys.exit(f"'{ns.find}' not found in the PDF text")
            page = hits[0]
            if len(hits) > 1:
                print(f"note: phrase also on pages {hits[1:8]}; rendering page {page}")
        else:
            page = ns.page
        if not 1 <= page <= len(doc):
            sys.exit(f"page {page} out of range (1–{len(doc)})")
        out = audit / "figures" / f"page-{page}.png"
        out.parent.mkdir(exist_ok=True)
        doc[page - 1].get_pixmap(dpi=ns.dpi).save(str(out))
    print(out.resolve())


if __name__ == "__main__":
    main()
