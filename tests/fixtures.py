"""Hand-written Spec and labels used by the core tests."""

from pathlib import Path

from agent.core.labels import Span
from agent.core.spec import FieldDef, RoleDef, SectionEdge, Spec

EXAMPLES = Path(__file__).resolve().parent.parent / "examples"


def mandala_spec() -> Spec:
    return Spec(
        unit_name="mantra",
        id_column="num",
        levels=["brahmana", "khanda"],
        fields=[
            FieldDef(name="upanishad", description="The mantra text."),
            FieldDef(name="bhashya", description="The commentary on the mantra, with its headings."),
        ],
        roles=[
            RoleDef(name="mula", description="Mantra text.", kind="unit", field="upanishad"),
            RoleDef(name="bhashya", description="Commentary.", kind="unit", field="bhashya"),
            RoleDef(name="heading", description="Side heading.", kind="attach_next", field="bhashya"),
            RoleDef(name="front_matter", description="Opening lines.", kind="attach_next", field="bhashya"),
            RoleDef(name="brahmana_title", description="Brahmana title.", kind="attach_next", field="bhashya",
                    section=SectionEdge(level="brahmana", edge="start")),
            RoleDef(name="khanda_end", description="इति … खण्डः", kind="attach_prev", field="bhashya",
                    section=SectionEdge(level="khanda", edge="end")),
            RoleDef(name="brahmana_end", description="इति … ब्राह्मणम्", kind="attach_prev", field="bhashya",
                    section=SectionEdge(level="brahmana", edge="end")),
            RoleDef(name="colophon", description="Closing verses and colophon.", kind="attach_prev", field="bhashya"),
            RoleDef(name="book_title", description="Book title.", kind="drop"),
            RoleDef(name="page_header", description="Running header.", kind="drop"),
            RoleDef(name="footnote", description="Footnote.", kind="drop"),
            RoleDef(name="noise", description="Printer's marks.", kind="drop"),
        ],
        labeling_guide="Mantra first, then its commentary; both end with the same number.",
    )


def _spans(rows: str) -> list[Span]:
    spans = []
    for row in rows.strip().splitlines():
        ids, role, *n = row.split()
        start, _, end = ids.partition("-")
        spans.append(Span(start=start, end=end or start, role=role, n=[int(x) for x in n]))
    return spans


# Pages 1–3 of the मण्डलब्राह्मण sample, labelled by hand.
MANDALA_PAGES_1_3 = _spans("""
P0001 book_title
P0002 front_matter
P0003 heading
P0004 brahmana_title
P0005-P0006 mula 1
P0007-P0013 bhashya 1
P0014 heading
P0015 mula 2
P0016 footnote
P0017 noise
P0018 page_header
P0019-P0022 bhashya 2
P0023 heading
P0024-P0025 mula 3
P0026-P0030 bhashya 3
P0031 heading
P0032-P0034 mula 4
P0035-P0040 bhashya 4
P0041 page_header
P0042-P0047 bhashya 4
P0048 heading
P0049 mula 5
P0050-P0051 mula 6
P0052-P0053 mula 7
P0054-P0055 mula 8
P0056 mula 9
P0057-P0058 mula 10
P0059-P0061 bhashya 5
P0062-P0063 bhashya 6
P0064-P0065 bhashya 7
P0066-P0067 bhashya 8
P0068-P0069 bhashya 9
P0070 bhashya 10
P0071 heading
P0072 mula 11
""")
