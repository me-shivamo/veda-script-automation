"""Books whose structure differs: page locators, out-of-order OCR, optional levels, section text, deep nesting."""

import pytest

from agent.core.assemble import build
from agent.core.fidelity import check_fidelity
from agent.core.labels import LabelStore, Span
from agent.core.pieces import make_pieces
from agent.core.spec import FieldDef, Level, RoleDef, SectionEdge, Spec, StructureEdit, apply_structure_edits, spec_diff


def chandogya_spec(position="counted", closing_field="bhashya", levels=None) -> Spec:
    return Spec(
        unit_name="mantra",
        levels=levels or [Level(name="adhyaya"), Level(name="khanda")],
        position=position,
        fields=[
            FieldDef(name="mantra", description="Root text."),
            FieldDef(name="bhashya", description="Commentary."),
            FieldDef(name="tippani", description="Footnotes on the commentary.", required=False, about="bhashya"),
        ],
        roles=[
            RoleDef(name="mula", description="Root text.", kind="unit", field="mantra"),
            RoleDef(name="bhashya", description="Commentary.", kind="unit", field="bhashya"),
            RoleDef(name="tippani", description="Footnote.", kind="unit", field="tippani"),
            RoleDef(name="header", description="Running header with location.", kind="locator"),
            RoleDef(name="khanda_end", description="इति … खण्डः", kind="attach_prev", field=closing_field,
                    section=SectionEdge(level="khanda", edge="end")),
            RoleDef(name="adhyaya_end", description="इति … अध्यायः", kind="attach_prev", field=closing_field,
                    section=SectionEdge(level="adhyaya", edge="end")),
            RoleDef(name="heading", description="Side heading.", kind="attach_next", field="bhashya"),
            RoleDef(name="noise", description="OCR junk.", kind="drop"),
        ],
        labeling_guide="test",
    )


def run(text, rows, spec):
    """rows: (role, n, at) per piece, in order."""
    pieces = make_pieces(text)
    assert len(pieces) == len(rows), [p.text for p in pieces]
    store = LabelStore()
    spans = [Span(start=p.id, end=p.id, role=role, n=n, at=at) for p, (role, n, at) in zip(pieces, rows)]
    _, errors = store.apply(spans, pieces, spec)
    assert not errors, errors
    result = build(pieces, store, spec)
    assert not check_fidelity(text, pieces, result.records + result.sections, result.dropped)
    return result


def errors_of(result):
    return [i.kind for i in result.errors]


# --- counted books with page locators ------------------------------------------------

EXTRACT = "\n".join([
    "अ. १ खं. ३ मं. ३ ] भाष्यसमेता । १९",
    "सा वाक् ॥३॥",
    "टीका ॥३॥",
    "इति प्रथमाध्याये तृतीयः खण्डः ।",
    "---",
    "अ. १ खं. ४ मं. १ ] भाष्यसमेता । २०",
    "ओमित्येतत् ॥१॥",
    "टीका ॥१॥",
])
EXTRACT_ROWS = [("header", [3], [1, 3]), ("mula", [3], []), ("bhashya", [3], []), ("khanda_end", [], []),
                ("header", [1], [1, 4]), ("mula", [1], []), ("bhashya", [1], [])]


def test_an_extract_is_numbered_from_its_first_locator():
    result = run(EXTRACT, EXTRACT_ROWS, chandogya_spec())
    assert [r.id for r in result.records] == ["1.3.3", "1.4.1"]
    assert result.records[0].fields["bhashya"] == "टीका ॥३॥ इति प्रथमाध्याये तृतीयः खण्डः ।"
    assert not result.errors  # the extract starting at mantra 3 is only a warning
    assert len(result.dropped) == 2  # locator lines are left out, and are not "dropped content"


def test_a_misread_closing_line_is_caught_by_the_locator():
    # 'इति प्रथमाध्याये षष्ठोऽध्यायः' really closes khanda 6, but read as an adhyaya close it moves the next mantra to 2.1.1.
    text = "अ. १ खं. ६ मं. १\nक ॥१॥\nटीका ॥१॥\n## इति प्रथमाध्याये षष्ठोऽध्यायः ।\n---\nअ. १ खं. ७ मं. १\nख ॥१॥\nटीका ॥१॥"
    rows = [("header", [], [1, 6]), ("mula", [1], []), ("bhashya", [1], []), ("adhyaya_end", [], []),
            ("header", [], [1, 7]), ("mula", [1], []), ("bhashya", [1], [])]
    result = run(text, rows, chandogya_spec())
    assert [r.id for r in result.records] == ["1.6.1", "2.1.1"]
    assert errors_of(result) == ["position_conflict"]
    rows[3] = ("khanda_end", [], [])
    fixed = run(text, rows, chandogya_spec())
    assert [r.id for r in fixed.records] == ["1.6.1", "1.7.1"] and not fixed.errors


def test_garbled_locator_is_ignored_with_a_warning():
    text = "अ. १ खं. ३\nक ॥१॥\n---\nश्रीमद्भगवद्गीता । [ अ. ७, सं. १ ]\nख ॥२॥\n---\nअ. १ खं. ३\nग ॥३॥"
    rows = [("header", [], [1, 3]), ("mula", [1], []), ("header", [], [7, 1]), ("mula", [2], []), ("header", [], [1, 3]), ("mula", [3], [])]
    spec = chandogya_spec()
    spec.fields[1].required = False
    result = run(text, rows, spec)
    assert [r.id for r in result.records] == ["1.3.1", "1.3.2", "1.3.3"]
    assert "locator_out_of_sequence" in [i.kind for i in result.issues] and not result.errors


# --- printed books: the OCR order is not trusted ----------------------------------------

def test_printed_position_places_out_of_order_text():
    # As OCR gives one page: commentary on 2 before commentary on 1, root texts after them, the closing line of
    # khanda 4 before the last words of mantra 5 of that khanda.
    text = "\n".join([
        "टीका द्वितीया ॥२॥",
        "टीका प्रथमा ॥१॥",
        "मूलं तृतीयम् ॥३॥",
        "अ. १ खं. ४ मं. १-३] भाष्यसमेता ।",
        "मूलं प्रथमम् ॥१॥",
        "मूलं द्वितीयम् ॥२॥",
        "टीका तृतीया ॥३॥",
        "इति प्रथमाध्याये चतुर्थः खण्डः ।",
        "पञ्चमं ॥४॥",
        "---",
        "छान्दोग्योपनिषत् । [ अ. १ खं. ५ मं. १ ]",
        "मूलं ॥१॥",
        "टीका ॥१॥",
    ])
    rows = [("bhashya", [2], [1, 4]), ("bhashya", [1], [1, 4]), ("mula", [3], [1, 4]), ("header", [1, 2, 3], [1, 4]),
            ("mula", [1], [1, 4]), ("mula", [2], [1, 4]), ("bhashya", [3], [1, 4]), ("khanda_end", [], [1, 4]),
            ("mula", [4], [1, 4]), ("header", [1], [1, 5]), ("mula", [1], [1, 5]), ("bhashya", [1], [1, 5])]
    spec = chandogya_spec("printed")
    spec.fields[1].required = False  # mantra 1.4.4 has no commentary on this page
    result = run(text, rows, spec)
    assert [r.id for r in result.records] == ["1.4.1", "1.4.2", "1.4.3", "1.4.4", "1.5.1"]
    by_id = {r.id: r.fields for r in result.records}
    assert by_id["1.4.1"] == {"mantra": "मूलं प्रथमम् ॥१॥", "bhashya": "टीका प्रथमा ॥१॥", "tippani": ""}
    # The closing line names khanda 4, so it goes to the last mantra of khanda 4, not to whatever was read before it.
    assert by_id["1.4.4"]["bhashya"] == "इति प्रथमाध्याये चतुर्थः खण्डः ।"
    assert not result.errors, result.errors


def test_printed_position_checks_at_against_the_locator():
    text = "अ. १ खं. ४\nक ॥१॥\nटीका ॥१॥"
    result = run(text, [("header", [], [1, 4]), ("mula", [1], [1, 3]), ("bhashya", [1], [1, 3])], chandogya_spec("printed"))
    assert errors_of(result) == ["position_conflict"]


def test_printed_position_needs_at_on_numbered_text_and_a_locator_role():
    spec = chandogya_spec("printed")
    pieces = make_pieces("क ॥१॥")
    _, errors = LabelStore().apply([Span(start="P0001", end="P0001", role="mula", n=[1])], pieces, spec)
    assert errors and "at" in errors[0]
    with pytest.raises(ValueError, match="locator"):
        Spec.model_validate({**spec.model_dump(), "roles": [r.model_dump() for r in spec.roles if r.kind != "locator"]})


def test_printed_numbering_is_checked_on_the_whole_set():
    text = "अ. १ खं. ४\nक ॥३॥\nटीका ॥३॥\nख ॥१॥\nटीका ॥१॥"
    rows = [("header", [], [1, 4]), ("mula", [3], [1, 4]), ("bhashya", [3], [1, 4]), ("mula", [1], [1, 4]), ("bhashya", [1], [1, 4])]
    result = run(text, rows, chandogya_spec("printed"))
    assert errors_of(result) == ["number_gap"]  # 2 is missing; 3 before 1 is only an order warning


# --- optional levels, section text, deep books -----------------------------------------

def test_optional_level_numbers_units_one_level_up():
    # Adhyaya 1 is divided into khandas, adhyaya 2 is not: its mantras are 2.1 and 2.2, not 2.1.1 and 2.1.2.
    spec = chandogya_spec(levels=[Level(name="adhyaya"), Level(name="khanda", optional=True)])
    text = "क ॥१॥\nटीका ॥१॥\nइति प्रथमः खण्डः\nख ॥१॥\nटीका ॥१॥\nइति द्वितीयः खण्डः\nइति प्रथमोऽध्यायः\nग ॥१॥\nटीका ॥१॥\nघ ॥२॥\nटीका ॥२॥"
    rows = [("mula", [1], []), ("bhashya", [1], []), ("khanda_end", [], []), ("mula", [1], []), ("bhashya", [1], []),
            ("khanda_end", [], []), ("adhyaya_end", [], []), ("mula", [1], []), ("bhashya", [1], []), ("mula", [2], []), ("bhashya", [2], [])]
    result = run(text, rows, spec)
    assert [r.id for r in result.records] == ["1.1.1", "1.2.1", "2.1", "2.2"]
    assert not result.errors


def test_section_text_can_be_kept_apart_from_the_units():
    levels = [Level(name="adhyaya"), Level(name="khanda", fields=[FieldDef(name="closing", description="Closing line.")])]
    spec = chandogya_spec(closing_field="khanda.closing", levels=levels)
    text = "क ॥१॥\nटीका ॥१॥\nइति प्रथमः खण्डः\nख ॥१॥\nटीका ॥१॥\nइति द्वितीयः खण्डः"
    rows = [("mula", [1], []), ("bhashya", [1], []), ("khanda_end", [], []), ("mula", [1], []), ("bhashya", [1], []), ("khanda_end", [], [])]
    result = run(text, rows, spec)
    assert [r.fields["bhashya"] for r in result.records] == ["टीका ॥१॥", "टीका ॥१॥"]
    assert [(s.id, s.level, s.fields) for s in result.sections] == [
        ("1.1", "khanda", {"closing": "इति प्रथमः खण्डः"}), ("1.2", "khanda", {"closing": "इति द्वितीयः खण्डः"})]


def test_five_levels_are_counted():
    names = ["kanda", "adhyaya", "brahmana", "khanda"]
    roles = [RoleDef(name="mula", description="x", kind="unit", field="text")]
    roles += [RoleDef(name=f"{n}_end", description="x", kind="attach_prev", field="text", section=SectionEdge(level=n, edge="end")) for n in names]
    spec = Spec(unit_name="mantra", levels=names, fields=[FieldDef(name="text", description="x")], roles=roles, labeling_guide="x")
    text = "क ॥१॥\nख ॥२॥\nइति खण्डः\nग ॥१॥\nइति ब्राह्मणम्\nघ ॥१॥\nइति अध्यायः\nङ ॥१॥\nइति काण्डम्\nच ॥१॥"
    rows = [("mula", [1], []), ("mula", [2], []), ("khanda_end", [], []), ("mula", [1], []), ("brahmana_end", [], []),
            ("mula", [1], []), ("adhyaya_end", [], []), ("mula", [1], []), ("kanda_end", [], []), ("mula", [1], [])]
    result = run(text, rows, spec)
    assert [r.id for r in result.records] == ["1.1.1.1.1", "1.1.1.1.2", "1.1.1.2.1", "1.1.2.1.1", "1.2.1.1.1", "2.1.1.1.1"]
    assert not result.issues


def test_other_script_junk_may_be_dropped():
    text = "क ॥१॥\nटीका ॥१॥\nஅன்புடைமை ஆன்ற குடிப்பிறத்தல் இவ்விரண்டும் பண்புடைமை என்னும் வழக்கு. ॥"
    pieces = make_pieces(text)
    assert "other-script" in pieces[2].hints
    result = run(text, [("mula", [1], []), ("bhashya", [1], []), ("noise", [], [])], chandogya_spec())
    assert not result.errors


# --- Specs: old files load, edits made by code ---------------------------------------

def test_specs_saved_with_level_names_still_load():
    old = chandogya_spec().model_dump()
    old["levels"] = ["adhyaya", "khanda"]
    del old["position"], old["output"]
    spec = Spec.model_validate(old)
    assert spec.level_names == ["adhyaya", "khanda"] and spec.position == "counted" and spec.output.shape == "table"


def test_structure_edits_are_applied_by_code_and_described():
    spec = chandogya_spec()
    edited = apply_structure_edits(spec, [
        StructureEdit(op="rename_column", column="mantra", to="mula"),
        StructureEdit(op="merge_column", column="tippani", to="bhashya"),
        StructureEdit(op="move_column", column="bhashya", index=0),
        StructureEdit(op="set_output", shape="tree", id_style="columns"),
    ])
    assert edited.columns == ["num", "bhashya", "mula"]
    assert edited.role("mula").field == "mula" and edited.role("tippani").field == "bhashya"
    changes = spec_diff(spec, edited)
    assert "Column mantra is now called mula." in changes and any("Output shape: tree" in c for c in changes)
    assert any("'tippani' now goes to bhashya" in c for c in changes) and not any("New column" in c for c in changes)
    with pytest.raises(ValueError):
        apply_structure_edits(spec, [StructureEdit(op="rename_column", column="mantra", to="bhashya")])


def test_a_passage_cut_off_by_a_broken_word_is_an_error():
    text = "अ. १ खं. ४\nस य एतदेवं प्रविशति तत्प्रवि-\nटीका ॥५॥\nइति प्रथमाध्याये चतुर्थः खण्डः ।\nश्य यदमृता देवास्तदमृतो भवति ॥५॥"
    rows = [("header", [], [1, 4]), ("mula", [5], [1, 4]), ("bhashya", [5], [1, 4]), ("khanda_end", [], [1, 4]), ("mula", [5], [1, 5])]
    spec = chandogya_spec("printed")
    spec.fields[1].required = False
    result = run(text, rows, spec)
    assert "passage_cut" in errors_of(result)
    rows[4] = ("mula", [5], [1, 4])  # the end of the word belongs to the same mantra
    assert "passage_cut" not in errors_of(run(text, rows, spec))


def test_garbled_bracketed_headers_may_be_dropped():
    text = "क ॥१॥\nटीका ॥१॥\n[अ. १, अ. २, अ. ३] : भाष्यकार ।"
    assert "header-like" in make_pieces(text)[2].hints
    assert not run(text, [("mula", [1], []), ("bhashya", [1], []), ("noise", [], [])], chandogya_spec()).errors


def test_devanagari_page_numbers_are_hinted_as_furniture():
    pieces = make_pieces("क ॥१॥\n२०\n३३.")
    assert ["digits-only" in p.hints for p in pieces] == [False, True, True]


def test_commentary_before_its_root_text_is_an_error_in_a_book_read_in_order():
    text = "तद्योगं च द्विधा विद्धि ।\nतारकं द्विविधम् ॥१॥\nटीका ॥१॥"
    rows = [("bhashya", [1], []), ("mula", [1], []), ("bhashya", [1], [])]
    assert "before_root" in errors_of(run(text, rows, chandogya_spec()))
    rows[0] = ("mula", [1], [])
    assert not run(text, rows, chandogya_spec()).errors


def test_printed_book_keeps_a_heading_with_the_next_unit():
    text = "अ. १ खं. ४\nक ॥१॥\nटीका ॥१॥\nशीर्षकम्\nख ॥२॥\nटीका ॥२॥"
    rows = [("header", [], [1, 4]), ("mula", [1], [1, 4]), ("bhashya", [1], [1, 4]), ("heading", [], [1, 4]),
            ("mula", [2], [1, 4]), ("bhashya", [2], [1, 4])]
    result = run(text, rows, chandogya_spec("printed"))
    assert [r.fields["bhashya"] for r in result.records] == ["टीका ॥१॥", "शीर्षकम् टीका ॥२॥"]
    assert not result.errors


def test_section_text_is_written_where_the_book_prints_it():
    from agent.export import block_rows, table_rows
    levels = [Level(name="adhyaya"), Level(name="khanda", fields=[FieldDef(name="closing", description="Closing line.")])]
    spec = chandogya_spec(closing_field="khanda.closing", levels=levels)
    text = "क ॥१॥\nटीका ॥१॥\nइति प्रथमः खण्डः\nख ॥१॥\nटीका ॥१॥\nइति द्वितीयः खण्डः"
    rows = [("mula", [1], []), ("bhashya", [1], []), ("khanda_end", [], []), ("mula", [1], []), ("bhashya", [1], []), ("khanda_end", [], [])]
    pieces = make_pieces(text)
    result = run(text, rows, spec)
    order = {p.id: i for i, p in enumerate(pieces)}
    columns, table = table_rows(result.records, spec, result.sections, order)
    assert columns == ["num", "mantra", "bhashya", "tippani", "khanda_closing"]
    assert [row[0] for row in table] == ["1.1.1", "1.1", "1.2.1", "1.2"]  # each closing line after its khanda
    _, blocks = block_rows(result.records, spec, result.sections, order)
    assert [(b[0], b[4]) for b in blocks][:3] == [("1.1.1", "mantra"), ("1.1.1", "bhashya"), ("1.1", "khanda.closing")]


def test_an_extract_starting_on_a_page_that_closes_a_section_is_numbered_by_the_later_locators():
    # The first page's header names khanda 4, but the page begins with the end of khanda 3.
    text = "\n".join(["प्रथमब्राह्मणे चतुर्थः खण्डः", "क ॥५॥", "टीका ॥५॥", "इति तृतीयः खण्डः", "ख ॥१॥", "टीका ॥१॥",
                      "---", "प्रथमब्राह्मणे चतुर्थः खण्डः", "ग ॥२॥", "टीका ॥२॥", "इति चतुर्थः खण्डः",
                      "---", "प्रथमब्राह्मणे पञ्चमः खण्डः", "घ ॥१॥", "टीका ॥१॥"])
    rows = [("header", [], [1, 4]), ("mula", [5], []), ("bhashya", [5], []), ("khanda_end", [], []), ("mula", [1], []), ("bhashya", [1], []),
            ("header", [], [1, 4]), ("mula", [2], []), ("bhashya", [2], []), ("khanda_end", [], []),
            ("header", [], [1, 5]), ("mula", [1], []), ("bhashya", [1], [])]
    result = run(text, rows, chandogya_spec())
    assert [r.id for r in result.records] == ["1.3.5", "1.4.1", "1.4.2", "1.5.1"]
    assert not result.errors
