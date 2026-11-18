"""HTML checks shared by the content security policy tests."""

from __future__ import annotations

import re

# A <script> element that executes inline code: no src and not a JSON data block.
_INLINE_SCRIPT = re.compile(
    r"<script(?![^>]*\bsrc=)(?![^>]*type=\"application/json\")[^>]*>",
    re.IGNORECASE,
)
