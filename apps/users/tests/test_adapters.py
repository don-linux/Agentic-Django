from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from django.test import override_settings

from apps.users.adapters import AccountAdapter

if TYPE_CHECKING:
    from django.test import RequestFactory


@pytest.mark.parametrize("allowed", [True, False])
def test_account_signup_follows_registration_setting(
    rf: RequestFactory,
    allowed: bool,  # noqa: FBT001
) -> None:
    with override_settings(ACCOUNT_ALLOW_REGISTRATION=allowed):
        assert AccountAdapter().is_open_for_signup(rf.get("/")) is allowed
