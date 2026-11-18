"""The error-report filter: hide every setting that can carry a credential."""

from __future__ import annotations

import re

from django.views.debug import SafeExceptionReporterFilter as DjangoFilter


class SafeExceptionReporterFilter(DjangoFilter):
    """
    Django's filter, plus settings whose name contains BROKER or DSN.

    A broker URL embeds the Valkey password, and SENTRY_DSN the project's key.
    See docs/security.md#exception-reports.
    """

    hidden_settings = re.compile(
        f"{DjangoFilter.hidden_settings.pattern}|BROKER|DSN",
        flags=re.IGNORECASE,
    )
