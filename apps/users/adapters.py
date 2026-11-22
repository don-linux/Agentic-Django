from __future__ import annotations

import dataclasses
import logging
import smtplib
import typing

from allauth.account.adapter import DefaultAccountAdapter
from allauth.headless.adapter import DefaultHeadlessAdapter
from django.conf import settings
from django.core.exceptions import ValidationError

from apps.users.managers import UserManager

if typing.TYPE_CHECKING:
    from django.contrib.auth.base_user import AbstractBaseUser
    from django.http import HttpRequest


logger = logging.getLogger(__name__)

# allauth mounts its phone routes whether or not a project stores phone numbers.
_PHONE_DISABLED = (
    "Phone numbers are not enabled in this template; see docs/auth/phone.md."
)


class AccountAdapter(DefaultAccountAdapter):
    def is_open_for_signup(self, request: HttpRequest) -> bool:
        return getattr(settings, "ACCOUNT_ALLOW_REGISTRATION", True)

    def clean_email(self, email: str) -> str:
        # Signup, login and add-email look up the same normalized key.
        return UserManager.normalize_email(super().clean_email(email))

    # Phone support is off: the template stores no phone numbers. Refusing the
    # number in the form makes POST /account/phone a 400; every other phone
    # route then finds no phone and no pending flow (200 with [] or 409). The
    # three methods below are unreachable once clean_phone refuses.
    @typing.override
    def clean_phone(self, phone: str) -> str:
        raise ValidationError(_PHONE_DISABLED, code="phone_not_supported")

    @typing.override
    def get_phone(self, user: AbstractBaseUser) -> tuple[str, bool] | None:
        return None

    @typing.override
    def get_user_by_phone(self, phone: str) -> AbstractBaseUser | None:
        return None

    @typing.override
    def set_phone(self, user: AbstractBaseUser, phone: str, verified: bool) -> None:
        raise NotImplementedError(_PHONE_DISABLED)

    @typing.override
    def set_phone_verified(self, user: AbstractBaseUser, phone: str) -> None:
        raise NotImplementedError(_PHONE_DISABLED)

    @typing.override
    def send_verification_code_sms(
        self, user: AbstractBaseUser, phone: str, code: str, **kwargs: typing.Any
    ) -> None:
        raise NotImplementedError(_PHONE_DISABLED)

    def send_mail(
        self,
        template_prefix: str,
        email: str,
        context: dict[str, typing.Any],
    ) -> None:
        # A failed send is logged, not raised: the row already exists, so a 500 would
        # claim the action failed. Only transport errors are caught. The log has the
        # error class only: the body holds the code, and the traceback of
        # SMTPRecipientsRefused holds the address. See docs/email.md#how-it-works.
        context = {
            # Defaults for base_message.txt; a value allauth already put in the
            # context wins. See docs/email.md#how-it-works.
            "email_signature_name": settings.EMAIL_SIGNATURE_NAME,
            "frontend_url": settings.FRONTEND_URL,
            **context,
        }
        try:
            super().send_mail(template_prefix, email, context)
        except (smtplib.SMTPException, OSError) as error:
            logger.error(  # noqa: TRY400
                "Could not send %s email: %s", template_prefix, type(error).__name__
            )


class HeadlessAdapter(DefaultHeadlessAdapter):
    """
    allauth headless payloads identify the user by its public UUID, never the pk.

    See docs/api.md#representation.
    """

    ID_DESCRIPTION = "The public user ID (UUID)."
    ID_EXAMPLE = "0196f0c8-5b8e-7c1a-9d2e-3f4a5b6c7d8e"

    def get_user_dataclass(self) -> type:
        fields: list[tuple[str, typing.Any, typing.Any]] = []
        for field in dataclasses.fields(super().get_user_dataclass()):
            if field.name == "id":
                metadata = {
                    "description": self.ID_DESCRIPTION,
                    "example": self.ID_EXAMPLE,
                }
                fields.append(("id", str | None, dataclasses.field(metadata=metadata)))
            else:
                fields.append(
                    (
                        field.name,
                        field.type,
                        dataclasses.field(metadata=field.metadata),
                    ),
                )
        return dataclasses.make_dataclass("User", fields)

    def user_as_dataclass(self, user: AbstractBaseUser) -> typing.Any:
        # DefaultHeadlessAdapter types `user` as AbstractBaseUser. Narrowing the
        # parameter to User is an incompatible override for pyright.
        data = super().user_as_dataclass(user)
        public_id = getattr(user, "public_id", None)
        return dataclasses.replace(data, id=str(public_id) if user.pk else None)
