import pytest

from google_docs import Content, NotFoundInDocument, RevisionConflict, bold, link, md
from google_docs.testing import FakeDocs, U


def test_append_after_a_heading_does_not_inherit_its_style(docs):
    doc = docs.create("Doc", "# Heading")
    docs.append(doc, "plain text")
    doc = docs.get(doc)
    assert [p.style_name for p in doc.paragraphs] == ["HEADING_1", "NORMAL_TEXT"]
    assert doc.to_markdown() == "# Heading\n\nplain text\n"


def test_append_after_a_list_does_not_continue_it(docs):
    doc = docs.create("Doc", "- a\n- b")
    docs.append(doc, "not a bullet")
    assert docs.get(doc).to_markdown() == "- a\n- b\n\nnot a bullet\n"


def test_append_after_bold_text_is_not_bold(docs):
    doc = docs.create("Doc", "**loud**")
    docs.append(doc, "quiet")
    assert docs.get(doc).to_markdown() == "**loud**\n\nquiet\n"


def test_prepend(docs):
    doc = docs.create("Doc", "Body")
    docs.prepend(doc, "# Top\n\nIntro")
    assert docs.get(doc).to_markdown() == "# Top\n\nIntro\n\nBody\n"


def test_prepend_to_empty_document(docs):
    doc = docs.create("Doc")
    docs.prepend(doc, "Only line")
    assert docs.get(doc).to_markdown() == "Only line\n"


def test_insert_after_heading(docs):
    doc = docs.create("Doc", "# A\n\na text\n\n# B\n\nb text")
    docs.insert_after(doc, "a", "- new\n- items")
    assert docs.get(doc).to_markdown() == (
        "# A\n\n- new\n- items\n\na text\n\n# B\n\nb text\n")


def test_insert_after_heading_followed_by_table(docs):
    doc = docs.create("Doc", "# Data\n\n| x |\n|---|\n| 1 |")
    docs.insert_after(doc, "Data", "Caption.")
    assert docs.get(doc).to_markdown() == "# Data\n\nCaption.\n\n| x |\n|---|\n| 1 |\n"


def test_replace_section_keeps_heading_and_following_sections(docs):
    doc = docs.create("Status", "# Status\n\nold\n\n- stale\n\n## Detail\n\nkeep? no\n\n"
                                "# Next\n\nkept")
    docs.replace_section(doc, "status", "All **green**.")
    assert docs.get(doc).to_markdown() == "# Status\n\nAll **green**.\n\n# Next\n\nkept\n"


def test_replace_last_section(docs):
    doc = docs.create("Doc", "# Intro\n\nhi\n\n# Log\n\n| a |\n|---|\n| 1 |\n\nold")
    docs.replace_section(doc, "Log", "- new entry")
    assert docs.get(doc).to_markdown() == "# Intro\n\nhi\n\n# Log\n\n- new entry\n"


def test_missing_heading(docs):
    doc = docs.create("Doc", "# A")
    with pytest.raises(NotFoundInDocument):
        docs.insert_after(doc, "Nope", "x")


def test_fill_plain_and_rich(docs):
    doc = docs.create("Offer", "# Offer for {{name}}\n\nDear {{name}},\n\n{{terms}}\n\n"
                               "Salary: {{salary}}\n\nRegards")
    result = docs.fill(doc, {
        "name": "Ayşe",
        "salary": md("**€90k** per year"),
        "terms": md("1. Remote\n2. Equity\n\n| Perk | Value |\n|---|---|\n| Days off | 30 |"),
        "missing": "x",
    })
    assert result.counts == {"name": 2, "salary": 1, "terms": 1, "missing": 0}
    assert docs.get(doc).to_markdown() == (
        "# Offer for Ayşe\n\nDear Ayşe,\n\n1. Remote\n2. Equity\n\n"
        "| Perk | Value |\n|---|---|\n| Days off | 30 |\n\n"
        "Salary: **€90k** per year\n\nRegards\n")


def test_fill_rich_inline_rejects_blocks(docs):
    doc = docs.create("Doc", "Total: {{x}} today")
    with pytest.raises(ValueError, match="line of its own"):
        docs.fill(doc, {"x": md("- a\n- b")})


def test_fill_placeholder_in_table_cell(docs):
    doc = docs.create("Doc", "| k | v |\n|---|---|\n| name | {{name}} |")
    docs.fill(doc, {"name": md("*Ada*")})
    assert docs.get(doc).tables[0].values == [["k", "v"], ["name", "Ada"]]
    assert "*Ada*" in docs.get(doc).to_markdown()


def test_replace_text_counts(docs):
    doc = docs.create("Doc", "cat Cat cat")
    assert docs.replace_text(doc, "cat", "dog") == 2
    assert docs.replace_text(doc, "CAT", "fox", match_case=False) == 1
    assert docs.get(doc).text == "dog fox dog"


def test_builder_api(docs):
    body = (Content()
            .title("Report")
            .paragraph("Grew ", bold("18%"), " — see ", link("sheet", "https://s.example"))
            .bullets(["a", (1, "a.1"), "b"])
            .numbered(["x", "y"])
            .table([["k", "v"], ["n", 3], ["nan", float("nan")]])
            .code("print('hi')")
            .page_break()
            .paragraph("after break"))
    doc = docs.create("Built", body)
    assert doc.paragraphs[0].style_name == "TITLE"
    assert doc.tables[0].values == [["k", "v"], ["n", "3"], ["nan", ""]]
    assert doc.to_markdown().startswith(
        "# Report\n\nGrew **18%** — see [sheet](https://s.example)\n\n- a\n  - a.1\n- b\n\n"
        "1. x\n2. y\n")


def test_tabs(fake, docs):
    doc_id = fake.add_document("Tabs", tabs=("Main", "Notes"))
    docs.append(doc_id, "# In notes", tab="notes")
    doc = docs.get(doc_id)
    assert [t.title for t in doc.tabs] == ["Main", "Notes"]
    assert doc.tab("Notes").to_markdown() == "# In notes\n"
    assert doc.to_markdown() == ""
    assert docs.markdown(doc_id, tab="Notes") == "# In notes\n"
    with pytest.raises(NotFoundInDocument):
        doc.tab("Missing")


def test_revision_conflict_is_retried_from_a_fresh_read():
    fake = FakeDocs()
    doc_id = fake.add_document()
    original = fake._handle
    raced = []

    def racy(request):
        if request.url.path.endswith(":batchUpdate") and not raced:
            raced.append(1)  # someone types "!" between our read and our write
            fake.documents[doc_id].tabs[0].units.insert(1, U("c", "!"))
            fake.edit_elsewhere(doc_id)
        return original(request)

    fake._handle = racy
    with fake.client() as docs:
        docs.append(doc_id, "hello")
        assert docs.get(doc_id).text == "!\nhello"
    batches = [r for r in fake.requests if r.url.path.endswith(":batchUpdate")]
    assert len(batches) == 2


def test_revision_conflict_gives_up_after_one_retry():
    fake = FakeDocs()
    doc_id = fake.add_document()
    original = fake._handle

    def always_racy(request):
        if request.url.path.endswith(":batchUpdate"):
            fake.edit_elsewhere(doc_id)
        return original(request)

    fake._handle = always_racy
    with fake.client() as docs, pytest.raises(RevisionConflict):
        docs.append(doc_id, "x")


def test_whole_markdown_document_is_one_batch(fake, docs):
    doc = docs.create("Doc")
    fake.requests.clear()
    docs.append(doc, "# A\n\n- b\n- c\n\n| d |\n|---|\n| e |\n\n```\nf\n```")
    methods = [(r.method, r.url.path.rsplit("/", 1)[-1]) for r in fake.requests]
    assert methods == [("GET", doc.id), ("POST", f"{doc.id}:batchUpdate")]


def test_emoji_before_table_keeps_indexes_right(docs):
    doc = docs.create("Doc", "😀😀😀\n\n| 👍 | b |\n|---|---|\n| c | 🎉 |\n\nafter 😀")
    assert doc.tables[0].values == [["👍", "b"], ["c", "🎉"]]
    assert doc.paragraphs[-1].text == "after 😀"


def test_table_after_list_leaves_no_stray_bullet(docs):
    doc = docs.create("Doc", "- a\n- b")
    docs.append(doc, "| x |\n|---|\n| 1 |")
    assert docs.get(doc).to_markdown() == "- a\n- b\n\n| x |\n|---|\n| 1 |\n"
    docs.append(doc, "after")
    assert docs.get(doc).to_markdown() == "- a\n- b\n\n| x |\n|---|\n| 1 |\n\nafter\n"


def test_two_tables_in_a_row(docs):
    doc = docs.create("Doc", "| a |\n|---|\n| 1 |\n\n| b | c |\n|---|---|\n| 2 | 3 |")
    assert [t.values for t in doc.tables] == [[["a"], ["1"]], [["b", "c"], ["2", "3"]]]


def test_private_use_characters_do_not_shift_indexes(docs):
    doc = docs.create("Doc", "icon  here **bold**\n\n| a |\n|---|\n| b |")
    assert doc.to_markdown() == "icon  here **bold**\n\n| a |\n|---|\n| b |\n"


def test_insert_after_leaves_the_heading_alone(fake, docs):
    doc = docs.create("Doc", "# Keep me\n\ntext")
    docs.insert_after(doc, "Keep me", "new")
    heading = docs.get(doc).paragraphs[0]
    assert (heading.text, heading.style_name, heading.end) == ("Keep me", "HEADING_1", 9)


def test_prepend_table_does_not_leave_a_styled_blank_line(docs):
    doc = docs.create("Doc", "# Heading first")
    docs.prepend(doc, "| a |\n|---|\n| 1 |")
    got = docs.get(doc)
    assert got.paragraphs[0].style_name == "NORMAL_TEXT"
    assert got.to_markdown() == "| a |\n|---|\n| 1 |\n\n# Heading first\n"


def test_prepend_before_a_leading_table_is_refused(docs, fake):
    from google_docs import DocsError

    doc = docs.create("Doc", "| a |\n|---|\n| 1 |")
    fake.documents[doc.id].tabs[0].units.pop(1)  # drop the empty paragraph above the table
    with pytest.raises(DocsError, match="starts with a table"):
        docs.prepend(doc, "x")


def test_anchor_links_keep_their_text(docs):
    doc = docs.create("Doc", "See [below](#details).")
    assert doc.to_markdown() == "See below.\n"
