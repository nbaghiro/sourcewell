"""Guard: no explicit `Any` anywhere under app/.

We enforce full strict typing with `mypy --strict`, but mypy's `disallow_any_explicit` can't be used
— it false-positives on every pydantic `BaseModel` subclass (the `Any` is inherited from the library
base, not our code). So we forbid `Any` by construction here: it greps the source for `Any` used as
an import or in a type position. Reach for `app.core.types.JsonObject` (+ `isinstance` narrowing),
a `TypedDict`, or a precise model instead.
"""

import re
from pathlib import Path

_BACKEND = Path(__file__).resolve().parent.parent
# Hardened source trees: the app + the demo seeder fixture (tests/seed).
_ROOTS = [_BACKEND / "app", _BACKEND / "tests" / "seed"]

# `Any` as an import, or in a type position (`: Any`, `[Any`, `Any]`, `Any,`, `-> Any`, `Any |`).
_PATTERNS = [
    re.compile(r"from typing import .*\bAny\b"),
    re.compile(r"\btyping\.Any\b"),
    re.compile(r"[:\[]\s*Any\b|->\s*Any\b|\bAny\s*[|\],]"),
]


def test_no_explicit_any_in_app_source() -> None:
    offenders: list[str] = []
    for path in sorted(p for root in _ROOTS for p in root.rglob("*.py")):
        for lineno, line in enumerate(path.read_text().splitlines(), 1):
            if any(p.search(line) for p in _PATTERNS):
                offenders.append(f"{path.relative_to(_BACKEND)}:{lineno}: {line.strip()}")
    assert not offenders, (
        "explicit `Any` found — use a precise type, a TypedDict, or app.core.types.JsonObject:\n"
        + "\n".join(offenders)
    )
