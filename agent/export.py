"""Write records to files, in the shape the Spec asks for.

Every shape is made from the same records and section records, so the text in
each is exactly what the fidelity check proved is the book's own: these
functions only arrange it.
- table: one row per unit (a spreadsheet)
- blocks: one row per piece of text (path, level numbers, unit, layer, text)
- tree: nested JSON in the book's own shape
"""

import csv
import json
from pathlib import Path

from .core.assemble import Record, SectionRecord, path_id
from .core.spec import Spec


def _plural(name: str) -> str:
    return name if name.endswith("s") else name + "s"


def _section_key(path: list[int], levels: int) -> tuple:
    return (*path, *([0] * (levels - len(path))))


def _units_in(section: SectionRecord, records: list[Record]) -> list[Record]:
    return [r for r in records if r.section[:len(section.path)] == section.path]


def section_after(section: SectionRecord, records: list[Record], order: dict[str, int] | None) -> bool:
    """True when this section text belongs after the section's units: closing lines, as the labels say, or else text
    the book prints after them."""
    filled = [name for name, text in section.fields.items() if text]
    if filled and all(name in section.ends for name in filled):
        return True
    if not order or any(name in section.ends for name in filled):
        return False
    own = [order[p] for ids in section.sources.values() for p in ids if p in order]
    units = [order[p] for r in _units_in(section, records) for ids in r.sources.values() for p in ids if p in order]
    return bool(own and units and min(own) > max(units))


def table_rows(records: list[Record], spec: Spec, sections: list[SectionRecord] = (),
               order: dict[str, int] | None = None) -> tuple[list[str], list[list[str]]]:
    levels = len(spec.levels)
    id_cols = [spec.id_column] if spec.output.id_style == "dotted" else spec.level_names + [spec.unit_name]
    field_names = [f.name for f in spec.fields]
    section_cols = spec.section_columns if sections else []
    columns = id_cols + field_names + section_cols
    by_path = {tuple(s.path): s for s in sections}

    def id_cells(path: list[int], n: int | None) -> list[str]:
        if spec.output.id_style == "dotted":
            return [path_id([*path, n] if n is not None else path)]
        cells = [str(x) if x else "" for x in path] + [""] * (levels - len(path))
        return cells + [str(n) if n is not None else ""]

    def section_cells(section_path: list[int], only: tuple | None = None) -> list[str]:
        cells = []
        for k, lv in enumerate(spec.levels):
            s = by_path.get(tuple(section_path[:k + 1]))
            for f in lv.fields:
                cells.append(s.fields.get(f.name, "") if s and (only is None or tuple(s.path) == only) else "")
        return cells

    keyed: list[tuple[tuple, list[str]]] = []
    for r in records:
        row = id_cells(r.section, r.n) + [r.fields.get(name, "") for name in field_names]
        if section_cols:
            row += section_cells(r.section) if spec.output.section_text == "columns" else [""] * len(section_cols)
        keyed.append(((*r.section, r.n), row))
    if section_cols and spec.output.section_text == "rows":
        for s in sections:
            row = id_cells(s.path, None) + [""] * len(field_names) + section_cells(s.path, only=tuple(s.path))
            units = sorted(_units_in(s, records), key=lambda r: (r.section, r.n))
            if units and section_after(s, records, order):
                keyed.append(((*units[-1].section, units[-1].n, 1), row))
            else:
                keyed.append(((*_section_key(s.path, levels), 0), row))
    keyed.sort(key=lambda item: item[0])
    return columns, [row for _, row in keyed]


def block_rows(records: list[Record], spec: Spec, sections: list[SectionRecord] = (),
               order: dict[str, int] | None = None) -> tuple[list[str], list[list[str]]]:
    """One row per text block. Section text goes before its section's first unit, or after its last unit when the
    book prints it after them (a closing line); `order` is each piece's reading position."""
    levels = len(spec.levels)
    columns = ["path"] + spec.level_names + [spec.unit_name, "layer", "text"]
    keyed: list[tuple[tuple, list[str]]] = []
    for r in records:
        for i, f in enumerate(spec.fields):
            text = r.fields.get(f.name, "")
            if text:
                keyed.append(((*r.section, r.n, 0, i), [r.id, *[str(x) if x else "" for x in r.section], str(r.n), f.name, text]))
    for s in sections:
        units = sorted(_units_in(s, records), key=lambda r: (r.section, r.n))
        unit_pieces = [order[p] for r in units for ids in r.sources.values() for p in ids if order and p in order]
        for i, (name, text) in enumerate(s.fields.items()):
            if not text:
                continue
            own = [order[p] for p in s.sources.get(name, []) if order and p in order]
            # Per field: a closing line goes after the section's units, a title before them.
            after = bool(units) and (name in s.ends or bool(own and unit_pieces and min(own) > max(unit_pieces)))
            anchor = units[-1] if after else (units[0] if units else None)
            key = (*anchor.section, anchor.n, 1 if after else -1, i) if anchor else (*_section_key(s.path, levels), 0, -1, i)
            cells = [str(x) if x else "" for x in s.path] + [""] * (levels - len(s.path))
            keyed.append((key, [s.id, *cells, "", f"{s.level}.{name}", text]))
    keyed.sort(key=lambda item: item[0])
    return columns, [row for _, row in keyed]


def book_tree(records: list[Record], spec: Spec, sections: list[SectionRecord] = ()) -> dict:
    """Nested in the book's own shape: {"adhyayas": [{"adhyaya": 1, "khandas": [{"khanda": 3, "mantras": [...]}]}]}."""
    root: dict = {}
    by_path = {tuple(s.path): s for s in sections}

    def node_for(path: list[int]) -> dict:
        node, sofar = root, []
        for k, number in enumerate(path):
            sofar.append(number)
            if number == 0:  # an optional level this section does not have
                continue
            name = spec.levels[k].name
            children = node.setdefault(_plural(name), [])
            if not children or children[-1][name] != number:
                child = {name: number}
                s = by_path.get(tuple(sofar))
                if s:
                    child.update({f.name: s.fields.get(f.name, "") for f in spec.levels[k].fields})
                children.append(child)
            node = children[-1]
        return node

    for r in sorted(records, key=lambda r: (r.section, r.n)):
        unit = {spec.unit_name: r.n, "id": r.id, **{f.name: r.fields.get(f.name, "") for f in spec.fields}}
        node_for(r.section).setdefault(_plural(spec.unit_name), []).append(unit)
    return {
        "structure": {
            "levels": [{"name": lv.name, "optional": lv.optional, "fields": [f.name for f in lv.fields]} for lv in spec.levels],
            "unit": spec.unit_name,
            "fields": [{"name": f.name, "about": f.about} for f in spec.fields],
        },
        **root,
    }


def _write_rows(path: Path, columns: list[str], rows: list[list[str]]) -> None:
    with open(path, "w", encoding="utf-8", newline="") as f:
        writer = csv.writer(f, quoting=csv.QUOTE_ALL, lineterminator="\n")
        writer.writerow(columns)
        writer.writerows(rows)


def write_csv(records: list[Record], spec: Spec, path: Path, sections: list[SectionRecord] = (), order: dict[str, int] | None = None) -> None:
    _write_rows(path, *table_rows(records, spec, sections, order))


def write_blocks(records: list[Record], spec: Spec, path: Path, sections: list[SectionRecord] = (), order: dict[str, int] | None = None) -> None:
    _write_rows(path, *block_rows(records, spec, sections, order))


def write_tree(records: list[Record], spec: Spec, path: Path, sections: list[SectionRecord] = ()) -> None:
    path.write_text(json.dumps(book_tree(records, spec, sections), ensure_ascii=False, indent=1) + "\n", encoding="utf-8")


def write_json(records: list[Record], spec: Spec, path: Path) -> None:
    rows = [{spec.id_column: r.id, **r.fields, "_pieces": r.sources} for r in records]
    path.write_text(json.dumps(rows, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")


def write_all(records: list[Record], spec: Spec, folder: Path, sections: list[SectionRecord] = (), order: dict[str, int] | None = None) -> dict[str, str]:
    """Every shape, plus the records for the web page. Returns {shape: file name}."""
    write_csv(records, spec, folder / "output.csv", sections, order)
    write_blocks(records, spec, folder / "output_blocks.csv", sections, order)
    write_tree(records, spec, folder / "output_tree.json", sections)
    write_json(records, spec, folder / "output.json")
    (folder / "sections.json").write_text(json.dumps([s.model_dump() | {"after": section_after(s, records, order)} for s in sections],
                                                     ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    return {"table": "output.csv", "blocks": "output_blocks.csv", "tree": "output_tree.json"}
