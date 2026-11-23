from __future__ import annotations

from typing import TYPE_CHECKING
from typing import Any
from typing import override

from allauth.headless.account.views import ManagePhoneView
from allauth.headless.base.response import AuthenticationResponse
from django.http import HttpResponseNotAllowed

if TYPE_CHECKING:
    from django.http import HttpRequest
    from django.http import HttpResponseBase


class PhoneView(ManagePhoneView):
    """
    allauth's account/phone view, without its 500 on unhandled methods.

    allauth only sets self.user for GET, POST or a signed-in user, then reads it
    for the rate limit. See docs/auth/phone.md.
    """

    _METHODS = ("GET", "POST")

    @override
    def dispatch(
        self, request: HttpRequest, *args: Any, **kwargs: Any
    ) -> HttpResponseBase:
        if request.method in self._METHODS:
            return super().dispatch(request, *args, **kwargs)
        if not request.user.is_authenticated:
            return AuthenticationResponse(request)
        return HttpResponseNotAllowed(self._METHODS)
