from django.contrib.auth import forms as admin_forms
from django.core.exceptions import ValidationError
from django.forms import EmailField

from .models import User


def _clean_unique_email(
    form: admin_forms.UserChangeForm | admin_forms.AdminUserCreationForm,
) -> str:
    email = User.objects.normalize_email(form.cleaned_data.get("email", ""))
    qs = User.objects.filter(email__iexact=email)
    if form.instance.pk:
        qs = qs.exclude(pk=form.instance.pk)
    if qs.exists():
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
