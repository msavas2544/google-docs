import io
import sys

import pytest

from google_docs import cli
from google_docs.testing import FakeDocs


@pytest.fixture
def fake(monkeypatch):
    fake = FakeDocs()
    monkeypatch.setattr(cli, "_client", lambda args: fake.client())
    return fake


def run(capsys, *argv):
    cli.main(list(argv))
    return capsys.readouterr()


def test_new_cat_append(fake, capsys, tmp_path, monkeypatch):
    src = tmp_path / "r.md"
    src.write_text("# Report\n\n- a\n", encoding="utf-8")
    url = run(capsys, "new", "Report", str(src)).out.strip()
    assert url.startswith("https://docs.google.com/document/d/")
    monkeypatch.setattr(sys, "stdin", io.StringIO("More **text**."))
    run(capsys, "append", url, "-")
    assert run(capsys, "cat", url).out == "# Report\n\n- a\n\nMore **text**.\n"
    assert run(capsys, "cat", url, "--text").out == "Report\na\nMore text.\n"
    assert "Tab 1" in run(capsys, "tabs", url).out


def test_fill_a_copy(fake, capsys, tmp_path):
    docs = fake.client()
    template = docs.create("T", "Hi {{name}}\n\n{{body}}\n\n{{nope}}")
    terms = tmp_path / "terms.md"
    terms.write_text("1. one\n2. two\n", encoding="utf-8")
    res = run(capsys, "fill", template.id, "--copy", "Copy", "--set", "name=Ada",
              "--md", f"body={terms}")
    assert "name: 1" in res.err and "body: 1" in res.err
    assert docs.markdown(res.out.strip()) == "Hi Ada\n\n1. one\n2. two\n\n{{nope}}\n"
    assert docs.get(template).text.startswith("Hi {{name}}")  # the template is untouched


def test_fill_warns_about_missing(fake, capsys):
    doc = fake.client().create("T", "nothing here")
    res = run(capsys, "fill", doc.id, "--set", "ghost=1")
    assert "not found in the document: ghost" in res.err


def test_export(fake, capsys, tmp_path):
    doc = fake.client().create("T", "plain")
    out = tmp_path / "doc.txt"
    run(capsys, "export", doc.id, str(out))
    assert out.read_bytes() == b"plain"


def test_errors_exit_cleanly(fake):
    with pytest.raises(SystemExit, match="error:"):
        cli.main(["cat", "x" * 30])
    with pytest.raises(SystemExit, match="key=value"):
        cli.main(["fill", "x" * 30, "--set", "novalue"])
