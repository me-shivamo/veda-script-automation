"""The extraction Spec: what one book's output looks like and which labels produce it.

The Analyst agent writes a Spec for each book. The Labeler labels pieces with
the Spec's roles, and code (assemble.py) turns labels into records.
"""

import re
from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator

_NAME = re.compile(r"^[a-z][a-z0-9_]*$")


class FieldDef(BaseModel):
    name: str = Field(description="Output column name, lower_snake_case, e.g. 'shloka', 'meaning', 'bhashya'.")
    description: str = Field(description="What this column holds.")
    required: bool = Field(default=True, description="True if every unit in this book should have this field. Section fields are never required.")
    about: str | None = Field(default=None, description=(
        "Set when this text comments on another column rather than on the unit itself, e.g. footnotes that explain "
        "the commentary: about='bhashya'. Leave null for root text, commentary and meaning."
    ))


class Level(BaseModel):
    name: str = Field(description="Section level name, lower_snake_case, e.g. 'adhyaya', 'khanda'.")
    optional: bool = Field(default=False, description=(
        "True if some sections of the level above have no sections of this level, e.g. a brahmana that is not "
        "divided into khandas. Its units are then numbered directly under the level above (4.1 instead of 4.1.1)."
    ))
    fields: list[FieldDef] = Field(default_factory=list, description=(
        "Columns that belong to a whole section of this level rather than to one unit, e.g. 'title' or 'closing'. "
        "Use them only when the user wants section text kept apart from the units."
    ))


class SectionEdge(BaseModel):
    level: str = Field(description="Which numbering level this line opens or closes; must be one of Spec.levels.")
    edge: Literal["start", "end"]


class RoleDef(BaseModel):
    name: str = Field(description="Label name, lower_snake_case, e.g. 'mula', 'bhashya', 'heading', 'page_header'.")
    description: str = Field(description="What this role is and how to recognise it in THIS book. Mention concrete cues.")
    kind: Literal["unit", "attach_next", "attach_prev", "locator", "drop"] = Field(description=(
        "unit: numbered content that belongs to one unit (the labeler gives its number). "
        "attach_next: unnumbered text kept with the unit that follows it. "
        "attach_prev: unnumbered text kept with the unit before it. "
        "locator: a page line that prints where the page is in the book, such as a running header 'अ. १ खं. ४ मं. १-३'; "
        "the labeler gives the section path it prints, code uses it to check numbering, and it is left out of the output. "
        "drop: page furniture that is removed from the output."
    ))
    field: str | None = Field(default=None, description=(
        "Output column this role's text goes into: a unit field such as 'bhashya', or a section field written as "
        "'<level>.<field>', e.g. 'khanda.closing'. Required unless kind is 'locator' or 'drop'."
    ))
    section: SectionEdge | None = Field(default=None, description="Set only for lines that open or close a numbering level, e.g. 'इति प्रथमः खण्डः' closes level 'khanda'.")
    examples: list[str] = Field(default_factory=list, description="IDs of one to three real pieces with this role, e.g. ['P0012']. Shown to the user next to the role.")


class TextOptions(BaseModel):
    """Code copies the book text exactly as printed: quotes, hyphens, stray marks and misread numbers all stay."""

    shared_text: Literal["copy", "first"] = Field(default="copy", description="Text that belongs to several units (॥ १२-१३ ॥): copy into each, or keep only in the first.")


class OutputShape(BaseModel):
    shape: Literal["table", "blocks", "tree"] = Field(default="table", description=(
        "table: one row per unit with one column per field (a spreadsheet). "
        "blocks: one row per piece of text (path, level numbers, unit, layer, text), for books with many layers. "
        "tree: nested JSON in the book's own shape (sections holding sections holding units)."
    ))
    id_style: Literal["dotted", "columns"] = Field(default="dotted", description="dotted: one ID column like 1.3.5. columns: one column per level plus the unit number.")
    section_text: Literal["rows", "columns"] = Field(default="rows", description=(
        "Only matters when levels have fields. rows: a row of its own for each section (ID 1.3). "
        "columns: each unit row repeats its section's fields."
    ))


class Spec(BaseModel):
    unit_name: str = Field(description="What one output row is, e.g. 'mantra', 'shloka', 'sutra'.")
    id_column: str = Field(default="num", description="Name of the ID column.")
    levels: list[Level] = Field(default_factory=list, description="Section levels above the unit, outermost first, e.g. adhyaya then khanda. Empty if units are numbered straight through the book.")
    restart_numbering_each_section: bool = Field(default=True, description="True if unit numbers start again at 1 in each innermost section.")
    position: Literal["counted", "printed"] = Field(default="counted", description=(
        "counted: code counts sections from the lines that open and close them, in reading order. "
        "printed: the labeler gives each unit's section path ('at') from the location the pages print (needs a locator role); "
        "use it when the OCR reading order is unreliable, e.g. boxed root text printed out of place."
    ))
    fields: list[FieldDef]
    roles: list[RoleDef]
    text: TextOptions = Field(default_factory=TextOptions)
    output: OutputShape = Field(default_factory=OutputShape)
    labeling_guide: str = Field(description="Book-specific guidance for the Labeler: the order things appear in, how to tell the roles apart, and traps seen in this book.")

    @field_validator("levels", mode="before")
    @classmethod
    def _levels_from_names(cls, value):
        """Specs saved before levels had options are plain lists of names."""
        return [{"name": v} if isinstance(v, str) else v for v in value or []]

    @model_validator(mode="after")
    def _check(self) -> "Spec":
        problems = []
        field_names = [f.name for f in self.fields]
        role_names = [r.name for r in self.roles]
        for label, names in (("field", field_names), ("role", role_names), ("level", self.level_names)):
            problems += [f"{label} name '{n}' must be lower_snake_case" for n in names if not _NAME.match(n)]
            problems += [f"{label} name '{n}' is used twice" for n in set(names) if names.count(n) > 1]
        for level in self.levels:
            names = [f.name for f in level.fields]
            problems += [f"section field '{level.name}.{n}' must be lower_snake_case" for n in names if not _NAME.match(n)]
        if not _NAME.match(self.id_column) or self.id_column in field_names:
            problems.append(f"id_column '{self.id_column}' must be lower_snake_case and differ from every field name")
        if not self.fields:
            problems.append("at least one field is needed")
        if not any(r.kind == "unit" for r in self.roles):
            problems.append("at least one role of kind 'unit' is needed")
        if self.levels and not self.levels[0].name:
            problems.append("levels need names")
        if self.levels and self.levels[0].optional:
            problems.append(f"the outermost level '{self.levels[0].name}' cannot be optional")
        for r in self.roles:
            if r.kind in ("drop", "locator") and r.field is not None:
                problems.append(f"role '{r.name}' is kind '{r.kind}' so its field must be null")
            if r.kind not in ("drop", "locator"):
                if r.field is None or (r.field not in field_names and not self.section_field(r.field)):
                    problems.append(f"role '{r.name}' needs a field that exists in fields, or '<level>.<field>' for a section field (got {r.field!r})")
                elif r.kind == "unit" and self.section_field(r.field):
                    problems.append(f"role '{r.name}' is numbered content, so it must go to a unit field, not the section field '{r.field}'")
            if r.section and r.section.level not in self.level_names:
                problems.append(f"role '{r.name}' refers to level '{r.section.level}', which is not in levels {self.level_names}")
            if r.section and r.kind in ("unit", "locator"):
                problems.append(f"role '{r.name}' marks a section edge, so its kind cannot be '{r.kind}'")
        if self.position == "printed" and not any(r.kind == "locator" for r in self.roles):
            problems.append("position 'printed' needs a role of kind 'locator' for the lines that print where a page is")
        if self.position == "printed" and not self.levels:
            problems.append("position 'printed' only makes sense when there are section levels")
        for f in self.fields:
            if f.required and not any(r.kind == "unit" and r.field == f.name for r in self.roles):
                problems.append(f"field '{f.name}' is required but no 'unit' role feeds it; add one or set required=false")
            if f.about is not None and f.about not in field_names:
                problems.append(f"field '{f.name}' is about '{f.about}', which is not a field")
        if problems:
            raise ValueError("; ".join(problems))
        return self

    def role(self, name: str) -> RoleDef | None:
        return next((r for r in self.roles if r.name == name), None)

    @property
    def level_names(self) -> list[str]:
        return [lv.name for lv in self.levels]

    def section_field(self, name: str | None) -> tuple[int, FieldDef] | None:
        """For 'khanda.closing': (index of level 'khanda', its field). None for unit fields."""
        level, dot, field = (name or "").partition(".")
        if not dot or level not in self.level_names:
            return None
        index = self.level_names.index(level)
        found = next((f for f in self.levels[index].fields if f.name == field), None)
        return (index, found) if found else None

    @property
    def section_columns(self) -> list[str]:
        """Section fields as column names: 'khanda_closing'."""
        return [f"{lv.name}_{f.name}" for lv in self.levels for f in lv.fields]

    @property
    def columns(self) -> list[str]:
        return [self.id_column] + [f.name for f in self.fields]


def render_spec(spec: Spec) -> str:
    """The Spec as markdown, for agent prompts and for showing to the user."""
    lines = [f"One output row per **{spec.unit_name}**."]
    if spec.levels:
        restart = "restart at 1 in each" if spec.restart_numbering_each_section else "run on across each"
        optional = [lv.name for lv in spec.levels if lv.optional]
        lines.append(f"Row ID = {' . '.join(spec.level_names)} . {spec.unit_name} number. Unit numbers {restart} {spec.levels[-1].name}.")
        if optional:
            lines.append(f"Optional levels (some sections do not have them, and their units are numbered one level up): {', '.join(optional)}.")
        if spec.position == "printed":
            lines.append("**Position: printed.** Give every numbered span `at`, the section path printed for that page "
                         f"({', '.join(spec.level_names)}), read from the page's locator line. Code places units by `at`, not by reading order.")
        else:
            lines.append("**Position: counted.** Code counts sections from the lines that open and close them, in reading order.")
    else:
        lines.append(f"Row ID = the {spec.unit_name} number; there are no sections above it.")
    lines += ["", "**Output columns**", ""]
    lines.append(f"- `{spec.id_column}`: the row ID (computed by code)")
    for f in spec.fields:
        about = f" Comments on `{f.about}`." if f.about else ""
        lines.append(f"- `{f.name}`{'' if f.required else ' (optional)'}: {f.description}{about}")
    for lv in spec.levels:
        lines += [f"- `{lv.name}.{f.name}` (one per {lv.name}): {f.description}" for f in lv.fields]
    lines += ["", "**Roles (the labels you may use)**", "", "| role | kind | goes to | what it is and how to recognise it |", "|---|---|---|---|"]
    for r in spec.roles:
        target = {"drop": "removed", "locator": "removed (used to check numbering)"}.get(r.kind, f"`{r.field}`")
        edge = f" Marks the {r.section.edge} of a {r.section.level}." if r.section else ""
        lines.append(f"| `{r.name}` | {r.kind} | {target} | {r.description}{edge} |")
    lines += [
        "", "**Text handling (done by code)**", "",
        "- The text is copied exactly as printed. No character is changed: quotes, hyphens, stray marks and misread "
        "numbers all stay. Only the `##` mark the OCR tool adds to headings is left out.",
        f"- Text shared by several units: {spec.text.shared_text}",
        "", "**Guide for this book**", "", spec.labeling_guide.strip(),
    ]
    return "\n".join(lines)


# --- what changed between two Specs, and edits the user can make without an agent ---

def spec_diff(old: Spec, new: Spec) -> list[str]:
    """Plain-language list of what changed, for showing a revised plan."""
    changes = []
    if old.unit_name != new.unit_name:
        changes.append(f"One row is now one {new.unit_name} (was {old.unit_name}).")
    if old.level_names != new.level_names:
        changes.append(f"Sections: {' → '.join(new.level_names) or 'none'} (was {' → '.join(old.level_names) or 'none'}).")
    for lv in new.levels:
        before = next((o for o in old.levels if o.name == lv.name), None)
        if before and before.optional != lv.optional:
            changes.append(f"{lv.name} is now {'optional' if lv.optional else 'always present'}.")
    if old.position != new.position:
        changes.append(f"Rows are now placed by {'the location printed on each page' if new.position == 'printed' else 'counting section lines'}.")
    old_cols, new_cols = [f.name for f in old.fields], [f.name for f in new.fields]
    added, removed = [c for c in new_cols if c not in old_cols], [c for c in old_cols if c not in new_cols]
    # A column whose text all moved to one new column was renamed.
    renamed = {}
    for r in new.roles:
        o = old.role(r.name)
        if o and o.field in removed and r.field in added and renamed.get(o.field, r.field) == r.field:
            renamed[o.field] = r.field
    for before, after in renamed.items():
        changes.append(f"Column {before} is now called {after}.")
    added = [c for c in added if c not in renamed.values()]
    removed = [c for c in removed if c not in renamed]
    if added:
        changes.append("New column(s): " + ", ".join(added) + ".")
    if removed:
        changes.append("Column(s) removed: " + ", ".join(removed) + ".")
    if not added and not removed and old_cols != new_cols:
        changes.append("Column order: " + ", ".join(new_cols) + ".")
    if old.id_column != new.id_column:
        changes.append(f"ID column renamed to {new.id_column}.")
    old_sec, new_sec = set(old.section_columns), set(new.section_columns)
    if new_sec - old_sec:
        changes.append("New section field(s): " + ", ".join(sorted(new_sec - old_sec)) + ".")
    if old_sec - new_sec:
        changes.append("Section field(s) removed: " + ", ".join(sorted(old_sec - new_sec)) + ".")
    for key, label in (("shape", "Output shape"), ("id_style", "ID style"), ("section_text", "Section text")):
        if getattr(old.output, key) != getattr(new.output, key):
            changes.append(f"{label}: {getattr(new.output, key)} (was {getattr(old.output, key)}).")
    old_roles = {r.name: r for r in old.roles}
    for r in new.roles:
        o = old_roles.get(r.name)
        if o is None:
            changes.append(f"New kind of text '{r.name}' → {r.field or 'removed'}.")
        elif (o.kind, o.field, o.section) != (r.kind, r.field, r.section) and renamed.get(o.field) != r.field:
            changes.append(f"'{r.name}' now goes to {r.field or ('removed' if r.kind in ('drop', 'locator') else r.kind)} (was {o.field or 'removed'}).")
    gone = [name for name in old_roles if not new.role(name)]
    if gone:
        changes.append("Kinds of text no longer used: " + ", ".join(gone) + ".")
    if old.text.shared_text != new.text.shared_text:
        changes.append(f"Shared commentary: {new.text.shared_text}.")
    return changes


class StructureEdit(BaseModel):
    """A change to the output structure that code makes directly, with no agent involved."""

    op: Literal["rename_column", "move_column", "merge_column", "set_output", "rename_id_column"]
    column: str | None = None
    to: str | None = None
    index: int | None = None
    shape: Literal["table", "blocks", "tree"] | None = None
    id_style: Literal["dotted", "columns"] | None = None
    section_text: Literal["rows", "columns"] | None = None


def apply_structure_edits(spec: Spec, edits: list[StructureEdit]) -> Spec:
    """Return a new Spec with the edits applied. Raises ValueError with a message for people."""
    data = spec.model_dump()
    for e in edits:
        names = [f["name"] for f in data["fields"]]
        if e.op in ("rename_column", "move_column", "merge_column") and e.column not in names:
            raise ValueError(f"There is no column '{e.column}'.")
        if e.op == "rename_column":
            new = (e.to or "").strip()
            if not _NAME.match(new) or new in names or new == data["id_column"]:
                raise ValueError(f"'{new}' cannot be a column name: use lowercase letters, digits and _, and a name not already used.")
            for f in data["fields"]:
                f["name"] = new if f["name"] == e.column else f["name"]
                f["about"] = new if f["about"] == e.column else f["about"]
            for r in data["roles"]:
                r["field"] = new if r["field"] == e.column else r["field"]
        elif e.op == "move_column":
            fields = data["fields"]
            item = fields.pop(names.index(e.column))
            fields.insert(max(0, min(e.index or 0, len(fields))), item)
        elif e.op == "merge_column":
            if e.to not in names or e.to == e.column:
                raise ValueError(f"Choose another existing column to put '{e.column}' into.")
            data["fields"] = [f for f in data["fields"] if f["name"] != e.column]
            for f in data["fields"]:
                f["about"] = None if f["about"] == e.column else f["about"]
            for r in data["roles"]:
                r["field"] = e.to if r["field"] == e.column else r["field"]
        elif e.op == "rename_id_column":
            new = (e.to or "").strip()
            if not _NAME.match(new) or new in names:
                raise ValueError(f"'{new}' cannot be the ID column name.")
            data["id_column"] = new
        elif e.op == "set_output":
            for key in ("shape", "id_style", "section_text"):
                if getattr(e, key) is not None:
                    data["output"][key] = getattr(e, key)
    return Spec.model_validate(data)
