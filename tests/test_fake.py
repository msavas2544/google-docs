"""The fake must reject what Google rejects, or tests against it prove nothing."""

import pytest

from google_docs import InvalidArgument


@pytest.fixture
def doc(docs):
    return docs.create("Doc", "ab\n\n| x |\n|---|\n| y |\n\ncd")


def send(docs, doc, *requests):
    return docs.batch_update(doc, list(requests))


def table_start(docs, doc):
    return docs.get(doc).tables[0].start


def test_cannot_insert_at_table_start(docs, doc):
    with pytest.raises(InvalidArgument, match="inside the bounds"):
        send(docs, doc, {"insertText": {"text": "x",
                                        "location": {"index": table_start(docs, doc)}}})


def test_cannot_delete_the_final_newline(docs, doc):
    end = docs.get(doc).body.end_index
    with pytest.raises(InvalidArgument, match="end of the segment"):
        send(docs, doc, {"deleteContentRange": {"range": {"startIndex": end - 2,
                                                          "endIndex": end}}})


def test_cannot_delete_part_of_a_table(docs, doc):
    t = docs.get(doc).tables[0]
    with pytest.raises(InvalidArgument, match="part of a table"):
        send(docs, doc, {"deleteContentRange": {"range": {"startIndex": t.start,
                                                          "endIndex": t.start + 3}}})


def test_cannot_delete_newline_before_table(docs, doc):
    t = docs.get(doc).tables[0]
    with pytest.raises(InvalidArgument, match="before a table"):
        send(docs, doc, {"deleteContentRange": {"range": {"startIndex": t.start - 1,
                                                          "endIndex": t.start}}})


def test_batch_is_atomic(docs, doc):
    before = docs.get(doc).to_markdown()
    with pytest.raises(InvalidArgument, match=r"requests\[1\]"):
        send(docs, doc,
             {"insertText": {"text": "ok", "location": {"index": 1}}},
             {"insertText": {"text": "bad", "location": {"index": 10_000}}})
    assert docs.get(doc).to_markdown() == before


def test_bullets_consume_nesting_tabs(docs):
    doc = docs.create("Doc")
    send(docs, doc,
         {"insertText": {"text": "a\n\tb\n\t\tc", "location": {"index": 1}}},
         {"createParagraphBullets": {"range": {"startIndex": 1, "endIndex": 9},
                                     "bulletPreset": "NUMBERED_DECIMAL_ALPHA_ROMAN"}})
    got = docs.get(doc)
    assert [(p.text, p.nesting_level) for p in got.paragraphs] == [("a", 0), ("b", 1), ("c", 2)]
    assert got.body.end_index == 7


def test_insert_text_counts_utf16(docs):
    doc = docs.create("Doc")
    send(docs, doc, {"insertText": {"text": "😀", "location": {"index": 1}}},
         {"insertText": {"text": "x", "location": {"index": 3}}})
    assert docs.get(doc).text == "😀x"
    with pytest.raises(InvalidArgument):  # index 2 is inside the emoji
        send(docs, doc, {"insertText": {"text": "y", "location": {"index": 2}}})


def test_table_layout_matches_the_live_api(docs):
    """Measured on docs.googleapis.com: a 3x2 table inserted at the end of a paragraph
    whose newline is at 126 spans 127-144, rows end at 143, cell paragraphs at
    130, 132, 135, 137, 140, 142, and the paragraph after it starts at 144."""
    doc = docs.create("Doc")
    send(docs, doc, {"insertText": {"text": "x" * 125, "location": {"index": 1}}},
         {"insertTable": {"rows": 3, "columns": 2, "location": {"index": 126}}})
    got = docs.get(doc)
    t = got.tables[0]
    assert (t.start, t.end) == (127, 144)
    assert [row[-1].end for row in t.rows] == [133, 138, 143]
    assert [c.paragraphs[0].start for row in t.rows for c in row] == [
        130, 132, 135, 137, 140, 142]
    assert got.body.content[-1].start == 144
