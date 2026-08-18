"""
Result primitives shared by every check.

These live in their own module so that jaws_checks can import Section at
module level. Previously Section lived in checks.py, which imported
jaws_checks, so jaws_checks had to do a deferred in-function import to break
the cycle. Nothing here imports from the rest of the package.
"""

from dataclasses import dataclass, field


@dataclass
class CheckItem:
    level: str      # 'pass' | 'warn' | 'fail' | 'info'
    message: str


@dataclass
class Section:
    title: str
    items: list = field(default_factory=list)   # list of CheckItem

    def add(self, level: str, msg: str):
        self.items.append(CheckItem(level=level, message=msg))

    def to_text(self) -> str:
        lines = [self.title, "=" * len(self.title)]
        for item in self.items:
            prefix = {"pass": "[OK]", "warn": "[WARN]", "fail": "[FAIL]", "info": "[INFO]"}.get(
                item.level, "[?]"
            )
            lines.append(f"{prefix}  {item.message}")
        return "\n".join(lines)


@dataclass
class Report:
    sections: list = field(default_factory=list)   # list of Section
    manual_checklist: str = ""
    screen_table_rows: list = field(default_factory=list)  # list of dicts
    parse_errors: list = field(default_factory=list)


def _screen_num(slide) -> str:
    """Short location label — scene.slide number (e.g. '3.4')."""
    return f"{slide.scene_number}.{slide.slide_number}"
