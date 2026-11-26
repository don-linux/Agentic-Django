# Login with a third-party provider

The template ships without `allauth.socialaccount`: no provider is configured, and accounts sign up with an email and a password. To add a provider (Google, GitHub, and so on):

1. Install the extra and relock: `django-allauth[headless,mfa,socialaccount]`, then `uv lock`.
2. Add `"allauth.socialaccount"` and the provider app (for example `"allauth.socialaccount.providers.google"`) to `THIRD_PARTY_APPS`, configure the provider in `SOCIALACCOUNT_PROVIDERS`, and run `migrate`.
3. Add `path("accounts/", include("allauth.urls"))` to `config/urls.py`: the provider callbacks live there even in headless mode.
4. Add a `socialaccount_login_error` page to `build_headless_frontend_urls` in `config/settings/base.py`.
5. Keep registration and the user's name consistent with email signup with an adapter in `apps/users/adapters.py`, set as `SOCIALACCOUNT_ADAPTER = "apps.users.adapters.SocialAccountAdapter"`:

   ```python
   class SocialAccountAdapter(DefaultSocialAccountAdapter):
       @override
       def is_open_for_signup(
           self,
           request: HttpRequest,
           sociallogin: SocialLogin,
       ) -> bool:
           return bool(getattr(settings, "ACCOUNT_ALLOW_REGISTRATION", True))

       @override
       def populate_user(
           self,
           request: HttpRequest,
           sociallogin: SocialLogin,
           data: dict[str, typing.Any],
       ) -> AbstractBaseUser:
           user = super().populate_user(request, sociallogin, data)
           # Fill User.name from the provider's name, or first and last name. The
           # base adapter types the user as Django's; this project's is User.
           if isinstance(user, User) and not user.name:
               if name := data.get("name"):
                   user.name = name
               elif first_name := data.get("first_name"):
                   user.name = first_name
                   if last_name := data.get("last_name"):
                       user.name += f" {last_name}"
           return user
   ```

   It needs `from allauth.socialaccount.adapter import DefaultSocialAccountAdapter`, `User` from `apps.users.models` at run time (the `isinstance`), and, under `TYPE_CHECKING`, `SocialLogin` from `allauth.socialaccount.models`. Both overrides carry `@override`, which pyright and mypy require ([../typing.md#overrides](../typing.md#overrides)).
6. Erase what the provider stored when a user is anonymized: `SocialAccount` (its `uid` and `extra_data`) and `SocialToken` hold personal data and credentials. Delete the user's rows in `user_anonymize`, with a test, as [../user-deletion/anonymize.md](../user-deletion/anonymize.md) asks of every app that stores data about users.
7. Add tests for the provider flow and for this adapter, and the client's provider redirect to Bruno if useful.

A database that had `allauth.socialaccount` installed keeps its tables after removal; drop them with `python manage.py migrate socialaccount zero` while the app is still installed.
