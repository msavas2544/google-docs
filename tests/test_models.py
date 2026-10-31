"""Reading real-shaped API responses (written from the Docs API reference, not the fake)."""

from google_docs import Document, utf16_len


def run(s, e, text, **style):
    return {"startIndex": s, "endIndex": e, "textRun": {"content": text, "textStyle": style}}


def para(s, e, elements, style="NORMAL_TEXT", bullet=None, **pstyle):
    p = {"elements": elements, "paragraphStyle": {"namedStyleType": style, **pstyle}}
    if bullet:
        p["bullet"] = bullet
    return {"startIndex": s, "endIndex": e, "paragraph": p}


API_DOC = {
    "documentId": "1AbCdEfGhIjKlMnOpQrStUvWxYz0123456789",
    "title": "Spec",
    "revisionId": "ALm37BV",
    "body": {"content": [
        {"endIndex": 1, "sectionBreak": {"sectionStyle": {}}},
        para(1, 6, [run(1, 6, "Spec\n")], "TITLE"),
        para(6, 15, [run(6, 15, "Overview\n")], "HEADING_1", headingId="h.abc"),
        para(15, 40, [
            run(15, 19, "See "),
            run(19, 23, "docs", link={"url": "https://d.example"}, underline=True),
            run(23, 25, ", "),
            {"startIndex": 25, "endIndex": 26, "person": {
                "personId": "p1", "personProperties": {"name": "Ada L", "email": "ada@x.io"}}},
            run(26, 28, " &"),
            {"startIndex": 28, "endIndex": 29, "richLink": {"richLinkProperties": {
                "title": "Roadmap", "uri": "https://docs.google.com/r"}}},
            run(29, 30, " "),
            run(30, 33, "run", weightedFontFamily={"fontFamily": "Courier New"}),
            {"startIndex": 33, "endIndex": 34, "footnoteReference": {
                "footnoteId": "kix.fn1", "footnoteNumber": "1"}},
            run(34, 40, " *ok*\n", bold=True),
        ]),
        para(40, 45, [run(40, 45, "Item\n")], bullet={"listId": "L1"}),
        para(45, 50, [run(45, 50, "Next\n")], bullet={"listId": "L1", "nestingLevel": 1}),
        para(50, 52, [{"startIndex": 50, "endIndex": 51, "horizontalRule": {}},
                      run(51, 52, "\n")]),
        para(52, 54, [{"startIndex": 52, "endIndex": 53,
                       "inlineObjectElement": {"inlineObjectId": "kix.obj"}},
                      run(53, 54, "\n")]),
        {"startIndex": 54, "endIndex": 66, "table": {"rows": 2, "columns": 1, "tableRows": [
            {"startIndex": 55, "endIndex": 60, "tableCells": [
                {"startIndex": 56, "endIndex": 60, "content": [para(57, 60, [
                    run(57, 60, "Ky\n", bold=True)])]}]},
            {"startIndex": 60, "endIndex": 66, "tableCells": [
                {"startIndex": 61, "endIndex": 66, "content": [para(62, 66, [
                    run(62, 66, "a|b\n")])]}]},
        ]}},
        para(66, 67, [run(66, 67, "\n")]),
    ]},
    "lists": {"L1": {"listProperties": {"nestingLevels": [
        {"glyphSymbol": "●"}, {"glyphType": "DECIMAL"}]}}},
    "inlineObjects": {"kix.obj": {"inlineObjectProperties": {"embeddedObject": {
        "title": "Chart", "imageProperties": {"contentUri": "https://lh3.example/x"},
        "size": {"width": {"magnitude": 200, "unit": "PT"}}}}}},
    "footnotes": {"kix.fn1": {"content": [para(0, 6, [run(0, 6, "Note.\n")])]}},
}


def test_structure():
    doc = Document.from_api(API_DOC)
    assert doc.url.endswith("/document/d/1AbCdEfGhIjKlMnOpQrStUvWxYz0123456789/edit")
    assert doc.tabs[0].id is None and doc.revision_id == "ALm37BV"
    assert [h.text for h in doc.headings] == ["Spec", "Overview"]
    assert doc.headings[0].heading_level == 0 and doc.headings[1].heading_id == "h.abc"
    item, nested = doc.paragraphs[3], doc.paragraphs[4]
    assert not item.is_ordered and nested.is_ordered and nested.nesting_level == 1
    img = doc.paragraphs[6].elements[0]
    assert img.uri == "https://lh3.example/x" and img.width_pt == 200
    assert doc.tables[0].values == [["Ky"], ["a|b"]]
    assert doc.tables[0].records() == [{"Ky": "a|b"}]
    assert doc.body.end_index == 67


def test_to_markdown():
    assert Document.from_api(API_DOC).to_markdown() == (
        "# Spec\n\n"
        "# Overview\n\n"
        "See [docs](https://d.example), [Ada L](mailto:ada@x.io) &[Roadmap]"
        "(https://docs.google.com/r) `run`[^1] **\\*ok\\***\n\n"
        "- Item\n  1. Next\n\n"
        "---\n\n"
        "![Chart](https://lh3.example/x)\n\n"
        "| Ky |\n|---|\n| a\\|b |\n\n"
        "[^1]: Note.\n")


def test_find_counts_utf16():
    doc = Document.from_api({"documentId": "x" * 20, "body": {"content": [
        {"endIndex": 1, "sectionBreak": {}},
        para(1, 9, [run(1, 9, "😀 hi hi\n")])]}})
    assert utf16_len("😀") == 2
    assert doc.find("hi") == [(4, 6), (7, 9)]
    assert doc.find("HI", match_case=False) == [(4, 6), (7, 9)]


def test_section_range():
    doc = Document.from_api({"documentId": "x" * 20, "body": {"content": [
        {"endIndex": 1, "sectionBreak": {}},
        para(1, 3, [run(1, 3, "A\n")], "HEADING_1"),
        para(3, 5, [run(3, 5, "a\n")]),
        para(5, 7, [run(5, 7, "B\n")], "HEADING_2"),
        para(7, 9, [run(7, 9, "b\n")]),
        para(9, 11, [run(9, 11, "C\n")], "HEADING_1"),
        para(11, 12, [run(11, 12, "\n")]),
    ]}})
    assert doc.body.section("a") == (1, 9)  # includes the nested H2
    assert doc.body.section("B") == (5, 9)
    assert doc.body.section("C") == (9, 11)


def test_tabs_shape():
    doc = Document.from_api({"documentId": "x" * 20, "title": "T", "tabs": [
        {"tabProperties": {"tabId": "t.0", "title": "One", "index": 0},
         "documentTab": {"body": {"content": [para(1, 3, [run(1, 3, "1\n")])]}},
         "childTabs": [{"tabProperties": {"tabId": "t.1", "title": "Child", "index": 0},
                        "documentTab": {"body": {"content": [para(1, 3, [run(1, 3, "2\n")])]}}}]},
    ]})
    assert [(t.title, t.level, t.parent_id) for t in doc.tabs] == [
        ("One", 0, None), ("Child", 1, "t.0")]
    assert doc.tab("child").text == "2"


def test_heading_links_in_both_api_shapes():
    doc = Document.from_api({"documentId": "x" * 20, "body": {"content": [para(1, 9, [
        run(1, 3, "a ", link={"heading": {"id": "h.new", "tabId": "t.0"}}),
        run(3, 5, "b ", link={"headingId": "h.old"}),
        run(5, 9, "c d\n", link={"bookmark": {"id": "id.bm"}}),
    ])]}})
    assert [e.link for e in doc.paragraphs[0].elements] == ["#h.new", "#h.old", "#id.bm"]
