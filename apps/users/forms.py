from allauth.account.models import EmailAddress
from django.contrib.auth import forms as admin_forms
from django.core.exceptions import ValidationError
from django.forms import EmailField

from .models import User


def _clean_unique_email(
    form: admin_forms.UserChangeForm | admin_forms.AdminUserCreationForm,
) -> str:
    email = User.objects.normalize_email(form.cleaned_data.get("email", ""))
    users = User.objects.filter(email__iexact=email)
    # Another user's verified secondary address counts too: the primary
    # address user_primary_email_sync creates is verified, and allauth allows
    # one verified EmailAddress per address (unique_verified_email).
    addresses = EmailAddress.objects.filter(email__iexact=email, verified=True)
    if form.instance.pk:
        users = users.exclude(pk=form.instance.pk)
        addresses = addresses.exclude(user_id=form.instance.pk)
    if users.exists() or addresses.exists():
        raise ValidationError(
            form.fields["email"].error_messages["unique"],
            code="unique",
        )
    return email


class UserAdminChangeForm(admin_forms.UserChangeForm):
    class Meta(admin_forms.UserChangeForm.Meta):
        model = User
        field_classes = {"email": EmailField}
        error_messages = {
            "email": {"unique": "A user with this email already exists."},
        }

    def clean_email(self) -> str:
        return _clean_unique_email(self)


class UserAdminCreationForm(admin_forms.AdminUserCreationForm):
    """Admin-only user creation; API signup goes through allauth headless."""

    class Meta(admin_forms.AdminUserCreationForm.Meta):
        model = User
        fields = ("email",)
        field_classes = {"email": EmailField}
        error_messages = {
            "email": {"unique": "A user with this email already exists."},
        }

    def clean_email(self) -> str:
        return _clean_unique_email(self)
