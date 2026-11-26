# Phone numbers and SMS

allauth mounts its phone routes, but the template's `AccountAdapter` (`apps/users/adapters.py`) refuses every number in `clean_phone` and stores none.

`config/urls.py` mounts `apps.users.views.PhoneView` on `account/phone` before allauth's include. allauth's own view answers 500 to a method other than `GET` or `POST` without a session; `PhoneView` answers 401 then, and 405 with a session. Keep it when phone numbers are on. Drop it once an allauth upgrade fixes `ManagePhoneView.dispatch`: `test_no_allauth_route_answers_500_to_an_anonymous_request` shows when.

To sign up or log in with a phone number and an SMS code:

1. Store the number. Add a field to `User` (for example `phone = models.CharField(max_length=20, blank=True)` plus a `UniqueConstraint` on non-empty values) or a separate model, and a named migration.
2. Implement the adapter methods allauth calls, replacing the inert ones: `clean_phone` (validate the E.164 format, or use a library such as `phonenumbers`), `get_phone(user)` returning `(number, verified)` or `None`, `set_phone(user, phone, verified)`, `set_phone_verified(user, phone)` and `get_user_by_phone(phone)`.
3. Send the codes. Implement `send_verification_code_sms(user, phone, code, **kwargs)` with an SMS provider such as Twilio, and `send_unknown_account_sms(phone, **kwargs)` if enumeration prevention stays on. Keep the provider's credentials in environment variables read in settings, and never log the code.
4. Turn the flows on in `config/settings/base.py`: `ACCOUNT_LOGIN_METHODS = {"email", "phone"}` and, to ask for a number at signup, add `"phone*"` to `ACCOUNT_SIGNUP_FIELDS`.
5. Update the phone tests in `apps/users/tests/http/test_phone.py`: they now expect a refused number and must expect a verification flow instead.
6. Erase the number when a user is anonymized: clear it in `user_anonymize`, with a test ([../user-deletion/anonymize.md](../user-deletion/anonymize.md)).

Every SMS costs money: keep allauth's rate limits for phone verification, and count the provider's spend.
