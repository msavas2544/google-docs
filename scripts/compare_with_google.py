"""Find where FakeDocs and the real Google Docs API disagree.

Plans the live-check Markdown, then sends the requests ONE AT A TIME to a real
document and to FakeDocs, comparing the two documents after every step. Prints
the first difference with both structures, then trashes the test document.

    python scripts/compare_with_google.py > compare_result.txt
"""

from __future__ import annotations

import json
import runpy
import sys
from pathlib import Path
from typing import Any, Dict, List

from google_docs import DocsClient, OAuthCredentials
from google_docs._plan import plan_blocks
from google_docs.cli import DEFAULT_TOKEN
from google_docs.content import as_content
from google_docs.testing import FakeDocs

ROOT = Path(__file__).resolve().parent.parent
SOURCE = runpy.run_path(str(ROOT / "examples" / "live_check.py"))["SOURCE"]


def outline(content: List[Dict[str, Any]], depth: int = 0) -> List[str]:
    """Element kinds and index ranges, one per line."""
    lines = []
    pad = "  " * depth
    for el in content:
        s, e = el.get("startIndex", 0), el.get("endIndex")
        if "paragraph" in el:
            text = "".join(pe.get("textRun", {}).get("content", "")
                           for pe in el["paragraph"].get("elements", []))
            lines.append(f"{pad}P {s}-{e} {text!r}")
        elif "table" in el:
            t = el["table"]
            lines.append(f"{pad}TABLE {s}-{e} {t.get('rows')}x{t.get('columns')}")
            for row in t.get("tableRows", []):
                lines.append(f"{pad}  ROW {row.get('startIndex')}-{row.get('endIndex')}")
                for cell in row.get("tableCells", []):
                    lines.append(f"{pad}    CELL {cell.get('startIndex')}-{cell.get('endIndex')}")
                    lines += outline(cell.get("content", []), depth + 3)
        elif "sectionBreak" in el:
            lines.append(f"{pad}SECTION {s}-{e}")
        else:
            lines.append(f"{pad}OTHER {s}-{e} {list(el)}")
    return lines


def body(client: DocsClient, doc_id: str) -> List[Dict[str, Any]]:
    data = client.get(doc_id).raw
    tab = (data.get("tabs") or [{}])[0].get("documentTab") or data
    return (tab.get("body") or {}).get("content") or []


def main() -> None:
    real = DocsClient(OAuthCredentials.load(DEFAULT_TOKEN))
    fake_server = FakeDocs()
    fake = fake_server.client()
    real_doc = real.create("google-docs compare (safe to delete)")
    fake_doc = fake.create("compare")
    print(f"real document: {real_doc.url}")
    requests = plan_blocks(as_content(SOURCE).blocks, 1, tail_empty=True).requests
    print(f"{len(requests)} requests\n")
    try:
        for n, req in enumerate(requests):
            kind = next(iter(req))
            try:
                real.batch_update(real_doc.id, [req])
            except Exception as exc:  # noqa: BLE001
                print(f"STEP {n} {kind} REJECTED BY GOOGLE: {exc}")
                print(json.dumps(req, ensure_ascii=False))
                print("\nGoogle document before this step:")
                print("\n".join(outline(body(real, real_doc.id))))
                return
            fake.batch_update(fake_doc.id, [req])
            r, f = outline(body(real, real_doc.id)), outline(body(fake, fake_doc.id))
            if r != f:
                print(f"STEP {n} {kind} DIVERGED")
                print(json.dumps(req, ensure_ascii=False)[:400])
                print("\n--- Google ---")
                print("\n".join(r))
                print("\n--- FakeDocs ---")
                print("\n".join(f))
                return
            print(f"step {n:2d} {kind}: same")
        print("\nALL STEPS MATCH")
        print("\n".join(outline(body(real, real_doc.id))))
    finally:
        real.trash(real_doc)
        print("\n(test document moved to trash)")


if __name__ == "__main__":
    sys.exit(main())
