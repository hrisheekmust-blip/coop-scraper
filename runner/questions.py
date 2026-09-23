"""A question as observed on a form, normalized without losing what it actually asks.

Normalization only folds case, whitespace, quotes and required-markers. It never drops words, so negations
("do NOT require"), qualifiers ("professional", "3+ years") and places ("relocate to Austin") survive into
matching. The exact label is kept for audit.
"""
from __future__ import annotations

import hashlib
import re
import unicodedata
from dataclasses import dataclass, field

CONTEXTUAL = re.compile(r"\b(this|our|us|here|role|position|company|employer|office|relocat\w*|commut\w*|salary|compensation|start date|availability|available|willing|agree|consent|certify|acknowledge|previously|ever|location|site)\b", re.I)


def norm(s: str) -> str:
    s = unicodedata.normalize("NFKC", str(s or ""))
    s = s.replace("’", "'").replace("‘", "'").replace("“", '"').replace("”", '"')
    s = re.sub(r"[✱*]", "", s)
    s = re.sub(r"\s+", " ", s).strip().lower()
    s = re.sub(r"\s*\((required|optional)\)$", "", s)
    return re.sub(r"[?:.\s]+$", "", s).strip()


@dataclass
class Question:
    label: str
    control: str = "text"            # text | textarea | select | radio | checkbox | combobox | date | number | file | boolean | email | tel | url
    options: list = field(default_factory=list)
    required: bool = False
    multiple: bool = False
    placeholder: str = ""
    context: str = ""                # section heading / surrounding text
    max_len: int | None = None
    ref: str = ""                    # observer control reference
    job_id: str = ""
    employer: str = ""
    job_title: str = ""

    @property
    def norm(self) -> str:
        return norm(self.label)

    @property
    def is_choice(self) -> bool:
        return bool(self.options) or self.control in ("select", "radio", "checkbox", "combobox", "boolean")

    def option_list(self) -> list[str]:
        if self.options:
            return [str(o) for o in self.options]
        return ["Yes", "No"] if self.control == "boolean" else []

    def contextual(self) -> bool:
        return bool(CONTEXTUAL.search(self.label))

    def fallback_key(self) -> str:
        """Key for a question no rule recognizes: exact meaning + options, scoped to the employer when the
        wording depends on context ("relocate to our Austin office" is not "relocate to Boston")."""
        opts = "|".join(sorted(norm(o) for o in self.option_list()))
        h = hashlib.sha1(f"{self.norm}||{opts}".encode()).hexdigest()[:10]
        scope = f"@{norm(self.employer)}" if self.contextual() and self.employer else ""
        return f"q:{self.norm[:80]}#{h}{scope}"

    def to_dict(self) -> dict:
        return {"label": self.label, "control": self.control, "options": self.option_list(), "required": self.required,
                "multiple": self.multiple, "placeholder": self.placeholder, "context": self.context[:300], "max_len": self.max_len,
                "employer": self.employer, "job_id": self.job_id, "job_title": self.job_title}

    @classmethod
    def from_dict(cls, d: dict) -> "Question":
        keys = cls.__dataclass_fields__.keys()
        return cls(**{k: v for k, v in d.items() if k in keys})
