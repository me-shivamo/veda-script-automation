"""The extraction Spec: what one book's output looks like and which labels produce it.

The Analyst agent writes a Spec for each book. The Labeler labels pieces with
the Spec's roles, and code (assemble.py) turns labels into records.
"""

import re
from typing import Literal

from pydantic import BaseModel, Field, model_validator

_NAME = re.compile(r"^[a-z][a-z0-9_]*$")


class FieldDef(BaseModel):
    name: str = Field(description="Output column name, lower_snake_case, e.g. 'shloka', 'meaning', 'bhashya'.")
    description: str = Field(description="What this column holds.")
    required: bool = Field(default=True, description="True if every unit in this book should have this field.")


class SectionEdge(BaseModel):
    level: str = Field(description="Which numbering level this line opens or closes; must be one of Spec.levels.")
    edge: Literal["start", "end"]


class RoleDef(BaseModel):
    name: str = Field(description="Label name, lower_snake_case, e.g. 'mula', 'bhashya', 'heading', 'page_header'.")
    description: str = Field(description="What this role is and how to recognise it in THIS book. Mention concrete cues.")
    kind: Literal["unit", "attach_next", "attach_prev", "drop"] = Field(description=(
        "unit: numbered content that belongs to one unit (the labeler gives its number). "
        "attach_next: unnumbered text kept with the unit that follows it. "
        "attach_prev: unnumbered text kept with the unit before it. "
        "drop: page furniture that is removed from the output."
    ))
    field: str | None = Field(default=None, description="Output column this role's text goes into. Required unless kind is 'drop'.")
    section: SectionEdge | None = Field(default=None, description="Set only for lines that open or close a numbering level, e.g. 'इति प्रथमः खण्डः' closes level 'khanda'.")


class TextOptions(BaseModel):
    line_end_hyphen: Literal["keep", "join"] = Field(default="keep", description="'keep' leaves 'विष- येन्द्रिय'; 'join' makes 'विषयेन्द्रिय'.")
    quotes: Literal["keep", "single"] = Field(default="keep", description="'single' turns double quotes into ' (useful for CSV).")
    fix_verse_numbers: bool = Field(default=True, description="Rewrite a verse number the OCR misread (e.g. ९ for ५) to the number the labeler assigned.")
    strip_leading_noise: bool = Field(default=True, description="Remove a stray bullet or dot at the start of a line.")
    shared_text: Literal["copy", "first"] = Field(default="copy", description="Text that belongs to several units (॥ १२-१३ ॥): copy into each, or keep only in the first.")


class Spec(BaseModel):
    unit_name: str = Field(description="What one output row is, e.g. 'mantra', 'shloka', 'sutra'.")
    id_column: str = Field(default="num", description="Name of the ID column.")
    levels: list[str] = Field(default_factory=list, description="Numbering levels above the unit, outermost first, e.g. ['brahmana', 'khanda']. Empty if units are numbered straight through the book.")
    restart_numbering_each_section: bool = Field(default=True, description="True if unit numbers start again at 1 in each innermost section.")
    fields: list[FieldDef]
    roles: list[RoleDef]
    text: TextOptions = Field(default_factory=TextOptions)
    labeling_guide: str = Field(description="Book-specific guidance for the Labeler: the order things appear in, how to tell the roles apart, and traps seen in this book.")

    @model_validator(mode="after")
    def _check(self) -> "Spec":
        problems = []
        field_names = [f.name for f in self.fields]
        role_names = [r.name for r in self.roles]
        for label, names in (("field", field_names), ("role", role_names), ("level", self.levels)):
            problems += [f"{label} name '{n}' must be lower_snake_case" for n in names if not _NAME.match(n)]
            problems += [f"{label} name '{n}' is used twice" for n in set(names) if names.count(n) > 1]
        if not _NAME.match(self.id_column) or self.id_column in field_names:
            problems.append(f"id_column '{self.id_column}' must be lower_snake_case and differ from every field name")
        if not self.fields:
            problems.append("at least one field is needed")
        if not any(r.kind == "unit" for r in self.roles):
            problems.append("at least one role of kind 'unit' is needed")
        for r in self.roles:
            if r.kind == "drop" and r.field is not None:
                problems.append(f"role '{r.name}' is kind 'drop' so its field must be null")
            if r.kind != "drop" and r.field not in field_names:
                problems.append(f"role '{r.name}' needs a field that exists in fields (got {r.field!r})")
            if r.section and r.section.level not in self.levels:
                problems.append(f"role '{r.name}' refers to level '{r.section.level}', which is not in levels {self.levels}")
            if r.section and r.kind == "unit":
                problems.append(f"role '{r.name}' marks a section edge, so its kind cannot be 'unit'")
        for f in self.fields:
            if f.required and not any(r.kind == "unit" and r.field == f.name for r in self.roles):
                problems.append(f"field '{f.name}' is required but no 'unit' role feeds it; add one or set required=false")
        if problems:
            raise ValueError("; ".join(problems))
        return self

    def role(self, name: str) -> RoleDef | None:
        return next((r for r in self.roles if r.name == name), None)

    @property
    def columns(self) -> list[str]:
        return [self.id_column] + [f.name for f in self.fields]


def render_spec(spec: Spec) -> str:
    """The Spec as markdown, for agent prompts and for showing to the user."""
    lines = [f"One output row per **{spec.unit_name}**."]
    if spec.levels:
        restart = "restart at 1 in each" if spec.restart_numbering_each_section else "run on across each"
        lines.append(f"Row ID = {' . '.join(spec.levels)} . {spec.unit_name} number. Unit numbers {restart} {spec.levels[-1]}.")
    else:
        lines.append(f"Row ID = the {spec.unit_name} number; there are no sections above it.")
    lines += ["", "**Output columns**", ""]
    lines.append(f"- `{spec.id_column}`: the row ID (computed by code)")
    lines += [f"- `{f.name}`{'' if f.required else ' (optional)'}: {f.description}" for f in spec.fields]
    lines += ["", "**Roles (the labels you may use)**", "", "| role | kind | goes to | what it is and how to recognise it |", "|---|---|---|---|"]
    for r in spec.roles:
        target = "removed" if r.kind == "drop" else f"`{r.field}`"
        edge = f" Marks the {r.section.edge} of a {r.section.level}." if r.section else ""
        lines.append(f"| `{r.name}` | {r.kind} | {target} | {r.description}{edge} |")
    t = spec.text
    lines += [
        "", "**Text handling (done by code)**", "",
        f"- line-end hyphens: {t.line_end_hyphen}; double quotes: {t.quotes}; misread verse numbers fixed: {'yes' if t.fix_verse_numbers else 'no'}; "
        f"stray leading bullets removed: {'yes' if t.strip_leading_noise else 'no'}; text shared by several units: {t.shared_text}",
        "", "**Guide for this book**", "", spec.labeling_guide.strip(),
    ]
    return "\n".join(lines)
