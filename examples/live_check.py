"""Check the library against the real Google Docs API with your own account.

    pip install google-docs
    google-docs login --client-secrets client_secret_XXX.json   # once
    python examples/live_check.py            # add --keep to keep the test document

Enable the Google Docs API and the Google Drive API in your Cloud project first.
The script creates a document, runs every write helper, reads the result back and
compares it with what we expect. Everything printed as OK was confirmed by Google.
"""

from __future__ import annotations

import sys
import time

from google_docs import Content, DocsClient, OAuthCredentials, bold, md
from google_docs.cli import DEFAULT_TOKEN

SOURCE = """# Live check

Inline: **bold**, *italic*, ~~strike~~, `code`, [link](https://example.com) and 😀 emoji.

## Lists

- one
- two
  - two.a
    - deep
- three

1. first
2. second
   1. inner

## Table

| Name | Score 🎯 |
|---|---|
| Ada | **10** |
| Linus | 9 |

> A quote.

```
code block line 1
code block line 2
```

---

Last paragraph.
"""

CHECKS = []


def check(name: str, got: object, want: object) -> None:
    ok = got == want
    CHECKS.append(ok)
    print(f"{'OK  ' if ok else 'FAIL'} {name}")
    if not ok:
        print(f"     want: {want!r}\n     got:  {got!r}")


def main() -> None:
    keep = "--keep" in sys.argv
    docs = DocsClient(OAuthCredentials.load(DEFAULT_TOKEN))
    started = time.time()
    doc = docs.create(f"google-docs live check {time.strftime('%Y-%m-%d %H:%M')}")
    print(f"Created {doc.url}\n")
    try:
        docs.append(doc, SOURCE)
        check("markdown round trip", docs.markdown(doc), SOURCE)

        docs.append(doc, "Appended **after** the round trip.")
        check("append", docs.markdown(doc).endswith(
            "Last paragraph.\n\nAppended **after** the round trip.\n"), True)

        docs.insert_after(doc, "Table", "Caption above the table.")
        check("insert after heading", "## Table\n\nCaption above the table.\n\n| Name" in
              docs.markdown(doc), True)

        docs.replace_section(doc, "Lists", md("- replaced\n- section"))
        check("replace section", "## Lists\n\n- replaced\n- section\n\n## Table" in
              docs.markdown(doc), True)

        docs.append(doc, "Dear {{name}}, see {{summary}} below.\n\n{{block}}")
        result = docs.fill(doc, {
            "name": "Ayşe",
            "summary": Content().paragraph(bold("the summary")),
            "block": md("| k | v |\n|---|---|\n| a | 1 |"),
        })
        check("fill counts", result.counts, {"name": 1, "summary": 1, "block": 1})
        check("fill result", docs.markdown(doc).endswith(
            "Dear Ayşe, see **the summary** below.\n\n| k | v |\n|---|---|\n| a | 1 |\n"), True)

        check("replace text", docs.replace_text(doc, "Last paragraph", "Final paragraph"), 1)

        final = docs.get(doc)
        check("table values", final.tables[-1].values, [["k", "v"], ["a", "1"]])

        pdf = docs.export(doc, "pdf")
        check("export pdf", pdf[:5], b"%PDF-")
    finally:
        if keep:
            print(f"\nKept {doc.url}")
        else:
            docs.trash(doc)
            print("\nMoved the test document to the trash (use --keep to keep it).")
    passed = sum(CHECKS)
    print(f"{passed}/{len(CHECKS)} checks passed in {time.time() - started:.1f}s")
    sys.exit(0 if passed == len(CHECKS) else 1)


if __name__ == "__main__":
    main()
