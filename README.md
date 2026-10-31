# google-docs

**Google Docs for Python.** Read any document as Markdown. Write Markdown into any document: headings, lists, tables, links and code, all in one atomic request. Fill templates, update sections, export PDFs.

```python
from google_docs import DocsClient, OAuthCredentials

docs = DocsClient(OAuthCredentials.load("token.json"))

doc = docs.create("Q3 report", """
# Q3 report

Revenue grew **18%**. Details are in [the sheet](https://example.com/q3).

| Region | Revenue | Growth |
|--------|---------|--------|
| North  | $1.24M  | +22%   |
| South  | $0.81M  | +9%    |

- Hiring is on track
  - 4 of 5 roles filled
- Next review: **October 30**
""")

print(doc.url)
print(docs.get(doc).to_markdown())   # and back again
```

## Why

The Google Docs API edits by **character index**. Every insert shifts everything after it. Tables have no "fill this cell" call, so you work out each cell's index by hand. Making a list nested means typing tabs that the API then deletes, which moves your text again. Indexes are counted in UTF-16, so one emoji throws off the rest of the request. Writing a single formatted paragraph takes a dozen requests that all have to agree with each other.

Google publishes no high-level Python client for Docs. `google-api-python-client` only hands you that raw request format. For Sheets, `gspread` fills this gap. This library aims to do the same for Docs.

## Install

```bash
pip install google-docs
pip install "google-docs[pandas]"   # optional: Table.to_pandas()
```

Python 3.9+. The only dependencies are `httpx` and `markdown-it-py`.

## Sign in

1. In the [Google Cloud console](https://console.cloud.google.com/apis/library), enable the **Google Docs API** and the **Google Drive API**.
2. Create an OAuth client of type *Desktop app* and download its `client_secret_*.json`.
3. Run:

```bash
google-docs login --client-secrets client_secret_XXX.json
```

The token is saved to `~/.config/google-docs/token.json` and refreshed automatically. `DocsClient` also accepts a raw access token string or any `google.auth` credentials object, such as a service account.

## Read

```python
doc = docs.get("https://docs.google.com/document/d/1AbC.../edit")   # an ID or a URL

doc.title, doc.url, doc.revision_id
doc.to_markdown()                # GitHub-flavoured Markdown
doc.text                         # plain text

for h in doc.headings:
    print("  " * (h.heading_level or 0), h.text)

table = doc.tables[0]
table.values                     # [["Region", "Revenue"], ["North", "$1.24M"], ...]
table.records()                  # [{"Region": "North", "Revenue": "$1.24M"}, ...]
table.to_pandas()

doc.find("TODO")                 # [(start, end), ...] index ranges
doc.tab("Appendix").to_markdown()  # documents with tabs
```

Everything is typed: `Paragraph`, `TextRun` (`.bold`, `.link`, `.is_code`...), `Table`, `Cell`, `InlineImage`, and `Chip` for people, smart links, dates and footnotes. Each object keeps its raw JSON in `.raw`.

> **For LLM and RAG pipelines:** `to_markdown()` keeps headings, lists, tables, links and footnotes, so a model sees the document's structure and not just a flat run of text.

## Write

Every write method takes **Markdown** or a **`Content`** object. Every write goes out as a single `batchUpdate`, so it either applies completely or not at all.

```python
docs.append(doc, "## Update\n\nShipped **v2**.")
docs.prepend(doc, "> Draft, do not share")
docs.insert_after(doc, "Risks", "- Vendor delay\n- Hiring freeze")
docs.replace_section(doc, "Status", "All systems **green** ✅")   # keeps the heading
docs.replace_text(doc, "2025", "2026")                           # returns the count
```

Build content in Python when it comes from data:

```python
from google_docs import Content, bold, italic, link, code

report = (
    Content()
    .title("Weekly sales")
    .paragraph("Total: ", bold("$2.7M"), " (", italic("+12% WoW"), ")")
    .heading("By region", 2)
    .table([["Region", "Revenue"], *rows])             # or [df.columns, *df.values]
    .bullets(["North", (1, "nested item"), "South"])   # (level, text) to nest
    .numbered(["first", "second"])
    .checklist(["ship it", "write the post"])
    .code("SELECT * FROM sales")
    .image("https://example.com/chart.png", width=400)
    .page_break()
)
docs.append(doc, report)
```

## Templates

Put `{{placeholders}}` in any Google Doc, then fill them:

```python
from google_docs import md

result = docs.fill(doc, {
    "name": "Ayşe Yılmaz",          # plain text: replaced everywhere, headers too
    "date": "30 October 2026",
    "terms": md("""
1. Fully remote
2. 30 days of paid leave

| Benefit | Value |
|---|---|
| Equity | 0.2% |
"""),                                # rich content: lists and tables go where {{terms}} was
})
result.counts                        # {"name": 3, "date": 1, "terms": 1}
```

Or copy a template and fill the copy in one call (this needs the Drive API):

```python
offer = docs.from_template(TEMPLATE_ID, "Offer – Ayşe Yılmaz", values)
docs.export(offer, "pdf", "offer.pdf")    # also docx, odt, rtf, txt, md, html, epub
```

## Async

`AsyncDocsClient` has exactly the same methods:

```python
from google_docs import AsyncDocsClient

async with AsyncDocsClient(creds) as docs:
    doc = await docs.create("Notes", "# Meeting notes")
    await docs.append(doc, summary_markdown)
```

Both clients run the same operation code. Each operation is written once, without I/O, and only the HTTP transport differs, so the sync and async clients can't drift apart.

## Test your own code without Google

`google_docs.testing.FakeDocs` is an in-memory Docs server. It models documents the way Google does: UTF-16 indexes, paragraphs that end in newlines, and table, row and cell markers. It rejects the same invalid requests Google rejects, such as inserting in the middle of a table or deleting the document's last newline.

```python
from google_docs.testing import FakeDocs

def test_weekly_report():
    fake = FakeDocs()
    docs = fake.client()              # or fake.async_client()
    doc = build_weekly_report(docs)   # your code
    assert docs.get(doc).tables[0].records()[0]["Region"] == "North"
```

## Command line

```bash
google-docs new "Release notes" CHANGELOG.md       # prints the URL
google-docs cat <doc> > doc.md                     # a document as Markdown
cat notes.md | google-docs append <doc> -
google-docs fill <template> --copy "Offer: Ayşe" --set name=Ayşe --md terms=terms.md
google-docs export <doc> report.pdf
google-docs tabs <doc>
```

## Markdown support

| Markdown | In Google Docs |
|---|---|
| `#` … `######` | Heading 1–6 |
| `**bold**` `*italic*` `~~strike~~` | Bold, italic, strikethrough |
| `` `code` `` and fenced code blocks | Roboto Mono on a light grey background |
| `[text](url)` | Link |
| `- item`, `1. item`, nested lists | Bulleted and numbered lists, up to 9 levels |
| `- [ ] task` | Checkbox list |
| Tables | Native tables; the header row is bold, cells can have formatting |
| `![alt](https://…)` on its own line | Inline image (the URL must be public) |
| `> quote` | Indented paragraph with a left border |
| `---` | A horizontal line, drawn as a paragraph border |
| Two trailing spaces, or `\` at line end | Line break inside the paragraph |

Reading back gives the same Markdown, so a Markdown → Docs → Markdown round trip is lossless for everything in this table. The test suite checks this. Some things can't round-trip: image alt text (the API can't set it), the checked state of checkboxes, and `#anchor` links (Docs heading IDs are generated), which are written as plain text.

## How it works

A planner turns your content into one list of requests:

- It inserts everything first, at a cursor that only moves forward, so every range it has already computed stays valid.
- It fills table cells last to first, using the index layout of a freshly inserted table.
- It resets inherited styles, because new text takes on the style of its neighbours (otherwise appending after a heading would give you another heading).
- It applies formatting next, then bullets from the bottom up, since bullets delete their nesting tabs and shift the text after them.

Every length is counted in UTF-16 units, as the API counts them.

Each write carries the revision ID it read. If someone edits the document in between, Google refuses the write and nothing is applied. The client then reads the document again and retries once. Rate limits and 5xx errors are retried with backoff.

## Scopes

| Scope | Needed for |
|---|---|
| `documents` | reading and writing documents |
| `drive.file` | `copy`, `export` and `trash` on documents your app created or opened |
| `drive` | the same on any document (`google-docs login --full-drive`) |

## Status

Version 0.1, alpha. 92 tests on Python 3.9–3.13 run against `FakeDocs`, whose index model was checked step by step against the live Docs API with [`scripts/compare_with_google.py`](scripts/compare_with_google.py). [`examples/live_check.py`](examples/live_check.py) passes all 9 checks against the real API (October 2026): Markdown round trip, append, insert after a heading, replace a section, rich template fill, replace text, tables and PDF export. Run it with your own account to check again.

This is a community project. It is not affiliated with or endorsed by Google.

## License

Apache-2.0
