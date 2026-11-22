from __future__ import annotations

from django.conf import settings

# config/settings/test.py: API_MAX_PAGE_NUMBER = 1000.
TEST_PAGE_CAP = 1000


def test_test_settings_pin_the_api_page_cap() -> None:
    assert settings.API_MAX_PAGE_NUMBER == TEST_PAGE_CAP
