# Changelog

## 0.1.0 — unreleased

- First release.
- Read any document as typed objects (tabs, headings, paragraphs, lists, tables, images, smart chips, footnotes) or as GitHub-flavoured Markdown.
- Write Markdown or `Content` into a document as one atomic `batchUpdate`: headings, bold/italic/strike/code/links, nested bullet, numbered and checkbox lists, tables with formatted cells, images, quotes, code blocks, rules and page breaks.
- `append`, `prepend`, `insert_after(heading)`, `replace_section(heading)`, `replace_text`.
- Templates: `fill` with plain values everywhere, or rich `Content` in place of a placeholder; `copy` and `from_template` through Drive.
- `export` to PDF, Word, ODT, RTF, text, Markdown, HTML and EPUB.
- Writes are guarded by the document revision and retried once from a fresh read if someone else edits at the same time.
- Sync and async clients built on one sans-I/O core; OAuth 2.0 with PKCE and refresh; retries with backoff.
- `google_docs.testing.FakeDocs`: an in-memory Docs server for your own tests.
- Verified against the live Docs API: `examples/live_check.py` passes, and `scripts/compare_with_google.py` shows FakeDocs matching Google request by request.
- `google-docs` CLI: `login`, `new`, `cat`, `tabs`, `append`, `fill`, `export`.
