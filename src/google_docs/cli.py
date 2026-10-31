"""``google-docs``: Google Docs from your terminal, in Markdown.

  google-docs login --client-secrets client_secret.json
  google-docs new "Weekly report" report.md          # prints the new document's URL
  google-docs cat <doc>                              # the document as Markdown
  google-docs append <doc> notes.md                  # or pipe Markdown into it with -
  google-docs fill <template> --copy "Offer: Ayse" --set name=Ayse --md terms=terms.md
  google-docs export <doc> report.pdf               # pdf, docx, odt, rtf, txt, md, epub
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

from .auth import DEFAULT_SCOPES, SCOPE_DRIVE, OAuthCredentials, OAuthFlow, load_client_secrets
from .client import DocsClient
from .content import md
from .errors import DocsError

DEFAULT_TOKEN = Path(os.environ.get(
    "GOOGLE_DOCS_TOKEN", Path.home() / ".config" / "google-docs" / "token.json"))


def _read(source: str) -> str:
    if source == "-":
        return sys.stdin.read()
    return Path(source).read_text(encoding="utf-8")


def _client(args: argparse.Namespace) -> DocsClient:
    token = Path(args.token)
    if not token.exists():
        sys.exit(f"No token at {token}. Run `google-docs login` first.")
    return DocsClient(OAuthCredentials.load(token))


def cmd_login(args: argparse.Namespace) -> None:
    cid = args.client_id or os.environ.get("GOOGLE_DOCS_CLIENT_ID")
    secret = args.client_secret or os.environ.get("GOOGLE_DOCS_CLIENT_SECRET")
    secrets_file = args.client_secrets or os.environ.get("GOOGLE_DOCS_CLIENT_SECRETS")
    if secrets_file:
        cid, secret = load_client_secrets(secrets_file)
    if not cid:
        sys.exit("--client-secrets FILE (or --client-id / GOOGLE_DOCS_CLIENT_ID) is required")
    scopes = list(DEFAULT_SCOPES) + ([SCOPE_DRIVE] if args.full_drive else [])
    flow = OAuthFlow(cid, secret, scopes)
    creds = flow.run_local_server(port=args.port, open_browser=not args.no_browser,
                                  print_fn=lambda m: print(m, file=sys.stderr))
    creds.save(args.token)
    print(f"Saved token to {args.token}", file=sys.stderr)


def cmd_cat(args: argparse.Namespace) -> None:
    with _client(args) as docs:
        doc = docs.get(args.doc)
        tab = doc.tab(args.tab) if args.tab else doc.tabs[0]
        sys.stdout.write(tab.text + "\n" if args.text else tab.to_markdown())


def cmd_tabs(args: argparse.Namespace) -> None:
    with _client(args) as docs:
        for t in docs.get(args.doc).tabs:
            print(f"{'  ' * t.level}{t.title}\t{t.id or ''}")


def cmd_new(args: argparse.Namespace) -> None:
    content = _read(args.file) if args.file else None
    with _client(args) as docs:
        doc = docs.create(args.title, content)
    print(doc.url)


def cmd_append(args: argparse.Namespace) -> None:
    with _client(args) as docs:
        result = docs.append(args.doc, _read(args.file), tab=args.tab)
    print(result.url)


def cmd_fill(args: argparse.Namespace) -> None:
    values: Dict[str, Any] = {}
    for item in args.set or []:
        key, sep, value = item.partition("=")
        if not sep:
            sys.exit(f"--set expects key=value, got {item!r}")
        values[key] = value
    for item in args.md or []:
        key, sep, path = item.partition("=")
        if not sep:
            sys.exit(f"--md expects key=FILE, got {item!r}")
        values[key] = md(_read(path))
    with _client(args) as docs:
        target = docs.copy(args.doc, args.copy).id if args.copy else args.doc
        result = docs.fill(target, values, tab=args.tab)
    for key, n in result.counts.items():
        print(f"  {key}: {n}", file=sys.stderr)
    missing = [k for k, n in result.counts.items() if n == 0]
    if missing:
        print(f"warning: not found in the document: {', '.join(missing)}", file=sys.stderr)
    print(result.url)


def cmd_export(args: argparse.Namespace) -> None:
    fmt = args.format or Path(args.out).suffix.lstrip(".") or "pdf"
    with _client(args) as docs:
        data = docs.export(args.doc, fmt, None if args.out == "-" else args.out)
    if args.out == "-":
        sys.stdout.buffer.write(data)
    else:
        print(f"Wrote {len(data):,} bytes to {args.out}", file=sys.stderr)


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="google-docs", description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--token", default=str(DEFAULT_TOKEN), help="token file (default: %(default)s)")
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("login", help="authorize in the browser and save a token")
    s.add_argument("--client-secrets", metavar="FILE",
                   help="client_secret_*.json downloaded from the Google Cloud console")
    s.add_argument("--client-id")
    s.add_argument("--client-secret")
    s.add_argument("--full-drive", action="store_true",
                   help="also ask for full Drive access (copy/export docs you didn't create)")
    s.add_argument("--port", type=int, default=0)
    s.add_argument("--no-browser", action="store_true")
    s.set_defaults(func=cmd_login)

    s = sub.add_parser("cat", help="print a document as Markdown")
    s.add_argument("doc", help="document ID or URL")
    s.add_argument("--tab", help="tab title or ID (default: the first tab)")
    s.add_argument("--text", action="store_true", help="plain text instead of Markdown")
    s.set_defaults(func=cmd_cat)

    s = sub.add_parser("tabs", help="list a document's tabs")
    s.add_argument("doc")
    s.set_defaults(func=cmd_tabs)

    s = sub.add_parser("new", help="create a document, optionally from a Markdown file")
    s.add_argument("title")
    s.add_argument("file", nargs="?", help="Markdown file, or - for stdin")
    s.set_defaults(func=cmd_new)

    s = sub.add_parser("append", help="append Markdown to a document")
    s.add_argument("doc")
    s.add_argument("file", help="Markdown file, or - for stdin")
    s.add_argument("--tab")
    s.set_defaults(func=cmd_append)

    s = sub.add_parser("fill", help="fill {{placeholders}} in a document or a copy of it")
    s.add_argument("doc")
    s.add_argument("--set", action="append", metavar="KEY=VALUE", help="plain text value")
    s.add_argument("--md", action="append", metavar="KEY=FILE",
                   help="rich value from a Markdown file (lists, tables...)")
    s.add_argument("--copy", metavar="TITLE", help="fill a copy with this title instead")
    s.add_argument("--tab")
    s.set_defaults(func=cmd_fill)

    s = sub.add_parser("export", help="download as PDF, Word, Markdown...")
    s.add_argument("doc")
    s.add_argument("out", help="output file (format from its extension), or - for stdout")
    s.add_argument("--format", help="override the format, e.g. docx")
    s.set_defaults(func=cmd_export)
    return p


def main(argv: Optional[List[str]] = None) -> None:
    args = build_parser().parse_args(argv)
    try:
        args.func(args)
    except (DocsError, ValueError, LookupError) as exc:
        sys.exit(f"error: {exc}")
    except KeyboardInterrupt:
        sys.exit(130)


if __name__ == "__main__":  # pragma: no cover
    main()
