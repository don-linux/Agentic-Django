"""
The password tests sign up, log in and set with.

Not a secret: it only ever reaches test databases. Defining it once keeps the
hardcoded-password checks (ruff S105/S106) meaningful everywhere else: a test
imports PASSWORD instead of writing a literal. See docs/development.md#test-fixtures.
"""

from __future__ import annotations

PASSWORD = "Correct-H0rse-Battery"  # noqa: S105
