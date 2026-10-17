from agent.core.assemble import build
from agent.core.duplicates import find_duplicate_pages
from agent.core.fidelity import check_fidelity
from agent.core.labels import LabelStore, Span
from agent.core.pieces import make_pieces, split_piece
from eval.score import load_gold, normalize

from fixtures import EXAMPLES, MANDALA_PAGES_1_3, mandala_spec


def kinds(result, severity=None):
    return [i.kind for i in result.issues if severity is None or i.severity == severity]


def labelled(text, rows, spec=None):
    """Label every piece of text in order with (role, n) rows and build in book scope."""
    spec = spec or mandala_spec()
    pieces = make_pieces(text)
    assert len(pieces) == len(rows), [p.text for p in pieces]
    store = LabelStore()
    spans = [Span(start=p.id, end=p.id, role=role, n=n) for p, (role, n) in zip(pieces, rows)]
    done, errors = store.apply(spans, pieces, spec)
    assert not errors, errors
    return pieces, store, build(pieces, store, spec)


# --- pieces ---------------------------------------------------------------

def test_line_is_cut_after_each_verse_marker():
    pieces = make_pieces("क ॥ ५ ॥ ख ग ॥ ६ ॥ घ\n॥ ७ ॥ ङ ॥८॥")
    assert [p.text for p in pieces] == ["क ॥ ५ ॥", "ख ग ॥ ६ ॥", "घ", "॥ ७ ॥", "ङ ॥८॥"]
    assert [p.printed_n for p in pieces] == [[5], [6], [], [7], [8]]


def test_punctuation_after_a_verse_marker_stays_on_its_line():
    pieces = make_pieces("क ॥ ४ ॥ -\nख ॥ ५ ॥ .\nग ॥ ६ ॥ - घ")
    assert [p.text for p in pieces] == ["क ॥ ४ ॥ -", "ख ॥ ५ ॥ .", "ग ॥ ६ ॥", "- घ"]
    assert [p.printed_n for p in pieces] == [[4], [5], [6], []]


def test_ranges_unnumbered_dandas_and_pages():
    pieces = make_pieces("अ ॥ १२-१३ ॥\nयोगे ॥ इति ॥ १० ॥\nउपास्महे ॥\n---\n२७४ शीर्षकम्\n85")
    assert pieces[0].printed_n == [12, 13]
    assert pieces[1].printed_n == [10]
    assert pieces[2].printed_n == []
    assert pieces[3].page == 2 and "page-top-number" in pieces[3].hints
    assert "digits-only" in pieces[4].hints


def test_split_piece_needs_one_exact_anchor():
    pieces = make_pieces("शीर्षकम् ते हीत्थमूचुः")
    a, b = split_piece(pieces, "P0001", "शीर्षकम्")
    assert (a.id, a.text, b.id, b.text) == ("P0001a", "शीर्षकम्", "P0001b", "ते हीत्थमूचुः")
    assert [p.id for p in pieces] == ["P0001a", "P0001b"]
    for bad in ("नास्ति", "ते हीत्थमूचुः"):
        try:
            split_piece(pieces, "P0001b", bad)
            assert False, "expected ValueError"
        except ValueError:
            pass


def test_duplicate_pages_found_in_first_sample():
    text = (EXAMPLES / "advaitamoda" / "input.md").read_text(encoding="utf-8")
    assert [(a, b) for a, b, _ in find_duplicate_pages(make_pieces(text))] == [(5, 10), (6, 11), (7, 12)]


# --- assembly -------------------------------------------------------------

def test_hand_labels_reproduce_expected_rows():
    text = (EXAMPLES / "mandala-brahmana" / "input.md").read_text(encoding="utf-8")
    pieces = [p for p in make_pieces(text) if p.page <= 3]
    store, spec = LabelStore(), mandala_spec()
    done, errors = store.apply(MANDALA_PAGES_1_3, pieces, spec)
    assert not errors and done == len(pieces)
    result = build(pieces, store, spec, scope="chunk")
    assert not result.errors, result.errors
    _, gold, _ = load_gold(EXAMPLES / "mandala-brahmana")
    got = {r.id: r.fields for r in result.records}
    for n in range(1, 11):
        num = f"1.1.{n}"
        assert {k: normalize(v) for k, v in got[num].items()} == gold[num], num
    assert not check_fidelity(text, pieces, result.records, result.dropped, pages=(1, 3))


def test_sections_are_counted_by_code():
    _, _, result = labelled(
        "क ॥ १ ॥\nटीका ॥ १ ॥\nइति प्रथमः खण्डः\nख ॥ १ ॥\nटीका ॥ १ ॥\nइति द्वितीयः खण्डः\nइति प्रथमं ब्राह्मणम्\nद्वितीयं ब्राह्मणम्\nग ॥ १ ॥\nटीका ॥ १ ॥",
        [("mula", [1]), ("bhashya", [1]), ("khanda_end", []), ("mula", [1]), ("bhashya", [1]), ("khanda_end", []),
         ("brahmana_end", []), ("brahmana_title", []), ("mula", [1]), ("bhashya", [1])],
    )
    assert [r.id for r in result.records] == ["1.1.1", "1.2.1", "2.1.1"]
    assert result.records[0].fields["bhashya"] == "टीका ॥ १ ॥ इति प्रथमः खण्डः"
    assert result.records[1].fields["bhashya"] == "टीका ॥ १ ॥ इति द्वितीयः खण्डः इति प्रथमं ब्राह्मणम्"
    assert result.records[2].fields["bhashya"] == "द्वितीयं ब्राह्मणम् टीका ॥ १ ॥"
    assert not result.issues


def test_shared_commentary_is_copied_to_each_unit():
    _, _, result = labelled(
        "क ॥ १ ॥\nख ॥ २ ॥\nटीका ॥ १-२ ॥",
        [("mula", [1]), ("mula", [2]), ("bhashya", [1, 2])],
    )
    assert [r.fields["bhashya"] for r in result.records] == ["टीका ॥ १-२ ॥"] * 2
    assert not result.issues


def test_misread_verse_number_places_the_row_but_the_text_is_kept():
    text = "क ॥ ४ ॥\nटीका ॥ ४ ॥\n. ख “उक्तम्” ॥ ९ ॥\nटीका ॥ ९ ॥"
    pieces, _, result = labelled(text, [("mula", [4]), ("bhashya", [4]), ("mula", [5]), ("bhashya", [5])])
    assert result.records[1].id == "1.1.5"
    assert result.records[1].fields == {"upanishad": ". ख “उक्तम्” ॥ ९ ॥", "bhashya": "टीका ॥ ९ ॥"}
    assert kinds(result) == ["section_start_number", "printed_number_differs", "printed_number_differs"]
    assert not result.errors
    assert not check_fidelity(text, pieces, result.records, result.dropped)


def test_fidelity_check_catches_any_change_to_the_text():
    text = "क ॥ १ ॥\nटीका  ॥ १ ॥\n---\n२७५ ग्रन्थनाम\nख ॥ २ ॥\nटीका ॥ २ ॥"
    pieces, _, result = labelled(text, [("mula", [1]), ("bhashya", [1]), ("page_header", []), ("mula", [2]), ("bhashya", [2])])
    assert result.records[0].fields["bhashya"] == "टीका  ॥ १ ॥"  # even a double space is kept
    assert not check_fidelity(text, pieces, result.records, result.dropped)
    altered = [r.model_copy(deep=True) for r in result.records]
    altered[1].fields["upanishad"] = "ख ॥ ३ ॥"
    assert [i.kind for i in check_fidelity(text, pieces, altered, result.dropped)] == ["not_verbatim"]
    assert check_fidelity(text, pieces, result.records, [])  # the page header is neither kept nor removed
    assert check_fidelity(text.replace("ख", "खा"), pieces, result.records, result.dropped)


def test_heading_goes_to_the_next_unit_and_markdown_mark_is_removed():
    _, _, result = labelled(
        "क ॥ १ ॥\nटीका ॥ १ ॥\n## शीर्षकम्\nख ॥ २ ॥\nटीका ॥ २ ॥",
        [("mula", [1]), ("bhashya", [1]), ("heading", []), ("mula", [2]), ("bhashya", [2])],
    )
    assert result.records[0].fields["bhashya"] == "टीका ॥ १ ॥"
    assert result.records[1].fields["bhashya"] == "शीर्षकम् टीका ॥ २ ॥"


def test_numbering_problems_are_errors():
    _, _, gap = labelled("क ॥ १ ॥\nख ॥ ३ ॥", [("mula", [1]), ("mula", [3])])
    assert "number_gap" in kinds(gap, "error")
    # A khanda-end line labelled as commentary: the numbers restart with no section edge.
    _, _, restart = labelled(
        "क ॥ १ ॥\nख ॥ २ ॥\nइति प्रथमः खण्डः\nग ॥ १ ॥",
        [("mula", [1]), ("mula", [2]), ("bhashya", [2]), ("mula", [1])],
    )
    assert "field_number_backwards" in kinds(restart, "error")
    _, _, missing = labelled("क ॥ १ ॥", [("mula", [1])])
    assert kinds(missing, "error") == ["missing_field"]


def test_unlabelled_and_suspicious_drops_are_reported():
    spec = mandala_spec()
    pieces = make_pieces("क ॥ १ ॥\nटीका ॥ १ ॥\n" + "ग" * 90)
    store = LabelStore()
    store.apply([Span(start="P0001", end="P0001", role="mula", n=[1]), Span(start="P0003", end="P0003", role="noise")], pieces, spec)
    result = build(pieces, store, spec)
    assert {"unlabelled", "dropped_content"} <= set(kinds(result, "error"))
    store.flag(["P0002", "P0003"], "cannot tell")
    # A flag lets a person decide an unlabelled piece, but it never makes dropping book text acceptable.
    errors = kinds(build(pieces, store, spec), "error")
    assert "dropped_content" in errors and "unlabelled" not in errors
    # Relabelling a flagged piece clears its flag, so the new label is checked afresh.
    store.apply([Span(start="P0003", end="P0003", role="noise")], pieces, spec)
    assert "dropped_content" in kinds(build(pieces, store, spec), "error")


def test_bad_spans_are_rejected_with_reasons():
    spec = mandala_spec()
    pieces = make_pieces("क ॥ १ ॥\nख")
    store = LabelStore()
    done, errors = store.apply([
        Span(start="P0001", end="P0001", role="mula"),
        Span(start="P0002", end="P0001", role="heading"),
        Span(start="P0001", end="P0009", role="heading"),
        Span(start="P0002", end="P0002", role="nonsense"),
        Span(start="P0002", end="P0002", role="heading", n=[4]),
    ], pieces, spec, allowed={"P0001", "P0002"})
    assert done == 1 and len(errors) == 4
    assert store.labels["P0002"].n == []


def test_page_header_kept_as_content_is_reported():
    _, _, result = labelled(
        "क ॥ १ ॥\nटीका\n---\n२७५ ग्रन्थनाम\nशेषः ॥ १ ॥",
        [("mula", [1]), ("bhashya", [1]), ("bhashya", [1]), ("bhashya", [1])],
    )
    assert kinds(result, "error") == ["kept_furniture"]
    assert "P0003" in result.issues[0].message


def test_chunk_that_starts_inside_a_group_is_not_an_error():
    # The chunk opens with mantras 13–14; the commentaries on 11 and 12–13 follow, whose mantras were in the previous chunk.
    spec = mandala_spec()
    pieces = make_pieces("ग ॥ १३ ॥\nघ ॥ १४ ॥\nटीका ॥ ११ ॥\nटीका ॥ १२-१३ ॥\nटीका ॥ १४ ॥")
    store = LabelStore()
    rows = [("mula", [13]), ("mula", [14]), ("bhashya", [11]), ("bhashya", [12, 13]), ("bhashya", [14])]
    store.apply([Span(start=p.id, end=p.id, role=r, n=n) for p, (r, n) in zip(pieces, rows)], pieces, spec)
    assert not build(pieces, store, spec, scope="chunk").errors
    assert "number_out_of_order" in kinds(build(pieces, store, spec, scope="book"), "error")


def test_heading_label_on_text_that_continues_a_line_is_reported():
    _, _, result = labelled(
        "क ॥ १ ॥ ख\nग ॥ २ ॥\nटीका ॥ १ ॥\nटीका ॥ २ ॥",
        [("mula", [1]), ("heading", []), ("mula", [2]), ("bhashya", [1]), ("bhashya", [2])],
    )
    assert kinds(result, "error") == ["attached_mid_line"]


def test_text_after_a_heading_or_section_close_cannot_attach_backwards():
    # The opening verse of khanda 2 was attached back to khanda 1, past its closing line and a heading.
    text = "क ॥ १ ॥\nटीका ॥ १ ॥\nइति प्रथमः खण्डः\nशीर्षकम्\nउक्तं च ।\nख ॥ १ ॥\nटीका ॥ १ ॥"
    rows = [("mula", [1]), ("bhashya", [1]), ("khanda_end", []), ("heading", []), ("colophon", []), ("mula", [1]), ("bhashya", [1])]
    _, _, wrong = labelled(text, rows)
    assert sorted(kinds(wrong, "error")) == ["order_broken", "text_after_section_end"]
    rows[4] = ("mula", [1])
    _, _, right = labelled(text, rows)
    assert not right.issues
    # A colophon after the last closing line is fine: the book ends there.
    _, _, end = labelled("क ॥ १ ॥\nटीका ॥ १ ॥\nइति प्रथमः खण्डः\nइति श्री… सम्पूर्णम्",
                         [("mula", [1]), ("bhashya", [1]), ("khanda_end", []), ("colophon", [])])
    assert not end.issues
    assert end.records[0].fields["bhashya"] == "टीका ॥ १ ॥ इति प्रथमः खण्डः इति श्री… सम्पूर्णम्"


def test_text_after_a_passage_number_cannot_keep_that_number():
    # 'शेषः' follows the marker ॥ १ ॥ of commentary 1, so it belongs to commentary 2.
    text = "क ॥ १ ॥\nख ॥ २ ॥\nटीका ॥ १ ॥ शेषः\nटीका ॥ २ ॥"
    _, _, wrong = labelled(text, [("mula", [1]), ("mula", [2]), ("bhashya", [1]), ("bhashya", [1]), ("bhashya", [2])])
    assert kinds(wrong, "error") == ["text_after_number"] and "P0004" in wrong.errors[0].message
    _, _, right = labelled(text, [("mula", [1]), ("mula", [2]), ("bhashya", [1]), ("bhashya", [2]), ("bhashya", [2])])
    assert not right.issues
    assert right.records[1].fields["bhashya"] == "शेषः टीका ॥ २ ॥"


def test_a_misread_number_still_ends_its_passage():
    # ॥ ११ ॥ is wrongly labelled 12, so the next line would join the same passage: two printed numbers in one passage.
    _, _, result = labelled(
        "क ॥ १० ॥\nख\nग ॥ ११ ॥\nघ ॥ १२ ॥",
        [("mula", [10]), ("mula", [11]), ("mula", [12]), ("mula", [12])],
    )
    assert "text_after_number" in kinds(result, "error")


def test_verse_line_labelled_as_heading_is_reported():
    _, _, result = labelled(
        "शीर्षकम्\nतद्योगं च द्विधा विद्धि ।\nक ॥ १ ॥\nटीका ॥ १ ॥\nइति प्रथमः खण्डः ॥",
        [("heading", []), ("heading", []), ("mula", [1]), ("bhashya", [1]), ("khanda_end", [])],
    )
    assert kinds(result, "error") == ["attached_sentence"] and result.errors[0].pieces == ["P0002"]
