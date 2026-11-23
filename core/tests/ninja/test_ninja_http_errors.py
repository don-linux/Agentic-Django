"""Where the installed Ninja raises HttpError, which the API codes by status.

core.api.exceptions gives an HttpError its code from the status alone: in
Ninja 1.8.0 a 400 is always a body Ninja cannot parse, and a 403 always a
failed CSRF check. A Ninja upgrade that raises HttpError anywhere else fails
here, so the new site is reviewed before it reaches clients with a code.
"""

from __future__ import annotations

from pathlib import Path

import ninja

NINJA_DIR = Path(ninja.__file__).parent
EXPECTED = [
    ("params/models.py", "raise HttpError(400, msg) from e"),
    ("security/apikey.py", 'raise HttpError(403, "CSRF check Failed")'),
]


def _http_error_calls() -> list[tuple[str, str]]:
    return sorted(
        (path.relative_to(NINJA_DIR).as_posix(), line.strip())
        for path in NINJA_DIR.rglob("*.py")
        for line in path.read_text().splitlines()
        if "HttpError(" in line
        and not line.lstrip().startswith(("class ", "def ", "#"))
        and "isinstance(" not in line
    )


def test_ninja_raises_http_error_only_for_body_parsing_and_csrf() -> None:
    assert _http_error_calls() == EXPECTED
