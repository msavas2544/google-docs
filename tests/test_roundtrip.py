"""Markdown -> Google Docs requests -> (fake) Docs -> Markdown must give back the input."""

import pytest

CASES = {
    "headings": "# Title\n\n## Section\n\n### Sub\n\nBody text.\n",
    "inline": "Plain, **bold**, *italic*, ~~gone~~, `code` and [a link](https://example.com).\n",
    "nested_bold_italic": "Mix of ***both*** styles.\n",
    "bullets": "- one\n- two\n  - two.a\n  - two.b\n    - deep\n- three\n",
    "numbered": "1. first\n2. second\n   1. inner\n   2. inner too\n3. third\n",
    "mixed_lists": "- bullet\n- bullet\n\n1. number\n2. number\n",
    "table": "| Name | Score |\n|---|---|\n| Ada | **10** |\n| Linus | 9 |\n",
    "table_then_text": "Intro.\n\n| a | b |\n|---|---|\n| 1 | 2 |\n\nOutro.\n",
    "emoji_everywhere": (
        "# 😀 Faces\n\n- 👍 good\n- 👎 bad\n\n| 🙂 | x |\n|---|---|\n| 😀 | **y** |\n\nEnd 🎉.\n"),
    "turkish": "# Çalışma Raporu\n\nŞirket **güçlü** büyüdü; İstanbul ofisi açıldı.\n",
    "quote": "> Stay hungry.\n\nAfter.\n",
    "code_block": "Run this:\n\n```\npip install google-docs\ngoogle-docs --help\n```\n\nDone.\n",
    "rule": "Above\n\n---\n\nBelow\n",
    "everything": (
        "# Q3 Report\n\nRevenue grew **18%**, see [the sheet](https://x.example/s).\n\n"
        "## Regions\n\n- North\n- South\n  - South-East\n\n| Region | Revenue |\n|---|---|\n"
        "| North | 1.2M |\n\n> Note\n\n```\ncode\n```\n\n---\n\nThe end.\n"
    ),
}


@pytest.mark.parametrize("source", CASES.values(), ids=CASES.keys())
def test_markdown_roundtrip(docs, source):
    doc = docs.create("Round trip", source)
    assert doc.to_markdown() == source


@pytest.mark.parametrize("source", CASES.values(), ids=CASES.keys())
def test_roundtrip_survives_a_second_append(docs, source):
    doc = docs.create("Twice", source)
    docs.append(doc, "Appended **after**.")
    assert docs.get(doc).to_markdown() == source + "\nAppended **after**.\n"


def test_task_list_becomes_checkboxes(docs, fake):
    doc = docs.create("Tasks", "- [ ] write\n- [x] test\n")
    assert [p.text for p in doc.paragraphs if p.is_list_item] == ["write", "test"]
    lists = fake.documents[doc.id].tabs[0].lists
    assert lists[doc.paragraphs[1].list_id]["listProperties"]["nestingLevels"][0][
        "glyphType"] == "GLYPH_TYPE_UNSPECIFIED"


def test_hard_break_is_a_soft_line_break(docs):
    doc = docs.create("Breaks", "line one  \nline two\n")
    assert doc.paragraphs[0].text == "line one\vline two"
    assert doc.to_markdown() == "line one  \nline two\n"


def test_image_paragraph(docs):
    doc = docs.create("Img", "Before\n\n![a cat](https://example.com/cat.png)\n\nAfter\n")
    img = doc.paragraphs[1].elements[0]
    assert img.uri == "https://example.com/cat.png"
    assert doc.to_markdown() == "Before\n\n![image](https://example.com/cat.png)\n\nAfter\n"
