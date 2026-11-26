# File uploads

The template has no file field yet; this is the convention for the first one. The helpers live in `core/files.py`.

## Field

```python
from django.core.validators import FileExtensionValidator

from core.files import MaxFileSizeValidator
from core.files import UploadTo

document = models.FileField(
    upload_to=UploadTo("invoices/document"),
    validators=[FileExtensionValidator(["pdf"]), MaxFileSizeValidator(5_000_000)],
)
```

- `UploadTo("<app>/<field>")` stores every file as `<app>/<field>/<uuid hex><.ext>`. The client's file name and any directory parts are dropped: names carry no personal data, cannot be guessed, and never collide, which is what makes the immutable `/media/` cache safe.
- If the product needs the original name, keep it in a separate `original_name` `CharField` and use it only in `Content-Disposition` when serving the file.
- Every field has an extension allow-list (`FileExtensionValidator`) and a size limit (`MaxFileSizeValidator`). Django has no built-in size validator.

`UploadTo` and `MaxFileSizeValidator` are deconstructible, so migrations can serialize them.

## Public or private

- Public (`default` storage, served by nginx at `MEDIA_URL`): only files meant to be world-readable, such as avatars or product images.
- Private (`storage=private_storage`): everything user-sensitive (IDs, invoices, contracts). When in doubt, private.

A private file is downloaded through an operation that looks it up with a selector scoped to the caller and returns `protected_file_response(...)`. With local storage behind nginx it answers with `X-Accel-Redirect` and nginx streams the file from an `internal` location; with S3 it redirects to a short-lived signed URL that names the file (`ResponseContentDisposition`); in development Django streams it. Never serialize a private file's `.url`. Before it writes `X-Accel-Redirect`, it refuses a stored name with `..`, a NUL byte or no file name (`SuspiciousFileOperation`: Django answers 400 and logs it to `django.security`), so a name planted in the database cannot point nginx elsewhere.

```python
# apps/invoices/models.py
from typing import TYPE_CHECKING

from django.core.validators import FileExtensionValidator
from django.db import models

from apps.users.models import User
from core.files import MaxFileSizeValidator
from core.files import UploadTo
from core.files import private_storage
from core.models import BaseModel

if TYPE_CHECKING:
    from django.db.models.expressions import Combinable


class Invoice(BaseModel):
    owner: models.ForeignKey[User | Combinable, User] = models.ForeignKey(
        User, on_delete=models.PROTECT
    )
    document: models.FileField = models.FileField(
        storage=private_storage,
        upload_to=UploadTo("invoices/document"),
        validators=[FileExtensionValidator(["pdf"]), MaxFileSizeValidator(10_000_000)],
    )


# apps/invoices/selectors.py
from typing import TYPE_CHECKING

from apps.invoices.models import Invoice

if TYPE_CHECKING:
    from uuid import UUID

    from apps.users.models import User


def invoice_visible(*, user: User, public_id: UUID) -> Invoice:
    """The caller's invoice; another user's or an unknown id is DoesNotExist (404)."""
    return Invoice.objects.filter(owner=user).get(public_id=public_id)


# apps/invoices/api/views.py
from django.http import HttpRequest
from django.http import HttpResponseBase
from ninja import Router

from apps.invoices.selectors import invoice_visible
from apps.users.models import User
from core.api.auth import request_user
from core.api.responses import LOOKUP_ERRORS
from core.api.responses import download_docs
from core.api.schemas import PublicIdPath
from core.files import protected_file_response

router = Router(tags=["invoices"])


@router.get(
    "/{public_id:id}/download/",
    response={200: None, 302: None, **LOOKUP_ERRORS},
    # The file's media type (the contract, and what Accept negotiates), and
    # the 302 to a signed URL when the file is on S3.
    openapi_extra=download_docs("application/pdf", LOOKUP_ERRORS),
    operation_id="invoices_download",
)
def invoices_download(
    request: HttpRequest, invoice_id: PublicIdPath
) -> HttpResponseBase:
    # Scoped to the owner, so another user's invoice is a 404.
    invoice = invoice_visible(user=request_user(request, User), public_id=invoice_id)
    return protected_file_response(invoice.document, filename="invoice.pdf")
```

`private_storage` is a callable, so migrations store its name. It returns the `LazyObject` that Django documents for a storage alias that tests override ([Using a callable](https://docs.djangoproject.com/en/6.1/topics/files/#using-a-callable)), reset when `STORAGES` changes so an override applies even after the proxy was first used. Do not replace it with a callable that returns `storages["private"]`: Django calls it once, when the models load, and `STORAGES` overrides would no longer reach the fields. Private files are served only through a view that checks permissions, which answers with `X-Accel-Redirect`. In development uploads go to `media/` at the repository root (`/app/media` in the container).

## Permissions

Django writes uploaded files with mode `0o644` and directories with `0o755` (`FILE_UPLOAD_PERMISSIONS`, `FILE_UPLOAD_DIRECTORY_PERMISSIONS`), so the host's nginx can read public files. A container umask of `027` without these settings would leave files nginx cannot read, and public media would answer 403.

## Endpoint

- The API accepts `application/json` bodies only (`core.api.parsers.JSONParser`), so an upload is a dedicated operation that takes `file: File[UploadedFile]` (both from `ninja`; Django's own `UploadedFile` class fails schema generation) and `Form[...]` fields instead of a JSON body ([Ninja: file uploads](https://django-ninja.dev/guide/files/)). Every operation with a JSON body answers a multipart body with 415 `unsupported_media_type`. Metadata next to the file goes in `Form[...]` fields, not a `Body` schema: Ninja parses a schema part of a multipart request through a request with no content type, which `core.api.parsers.JSONParser` answers with 415 (`ninja/params/models.py` in 1.8.0).
- Pass the upload operation `response=` and `openapi_extra=response_docs(...)` like any other; an operation that returns an `HttpResponse` (a download) keeps it as is ([Ninja: returning an HttpResponse](https://django-ninja.dev/guide/responses/#returning-a-django-httpresponse)).
- A download declares `response={200: None, 302: None, **LOOKUP_ERRORS}` and `openapi_extra=download_docs(<media type>, LOOKUP_ERRORS)` from `core.api.responses`. The 200 carries the file's media type, which `ApiContractMiddleware` negotiates `Accept` against: `Accept: application/pdf` gets the file, `Accept: application/json` a 406. The 302 is the redirect to a signed URL on S3, whose `ResponseContentDisposition` names the file (`core.files.protected_file_response`).
- The output schema returns the file's absolute URL, built from the request when the view has one.

## Images

- Use `ImageField`, and add Pillow (latest release, pinned with `==`) only then: it checks that the file really decodes as an image.
- Keep `Image.MAX_IMAGE_PIXELS` at its default, which protects against decompression bombs, and consider re-encoding uploads to strip EXIF data such as GPS coordinates.
- Never accept SVG or HTML: they can run script when opened.

## Limits and content type

- The per-field limit must be at most the proxy's `client_max_body_size` (20 MB in the nginx example). Above it, nginx answers 413 `request_too_large` before Django sees the request.
- Do not trust the client's `Content-Type`. The extension allow-list plus the `nosniff`, sandbox CSP and forced-download headers on `/media/` (see [NEXT-STEPS.md](NEXT-STEPS.md)) decide how a file is served.

Django keeps up to 2.5 MB of a request body or an upload in memory (`DATA_UPLOAD_MAX_MEMORY_SIZE`, `FILE_UPLOAD_MAX_MEMORY_SIZE`) and accepts at most 100 files per request (`DATA_UPLOAD_MAX_NUMBER_FILES`). The three are set explicitly, at Django's defaults, in `config/settings/base.py`: change them there, and keep the proxy's `client_max_body_size` above the largest upload you allow. There is no global file-size cap: each field sets its own with `MaxFileSizeValidator`.

## Replacing and deleting files

Django never deletes the stored file when its row is deleted or the field gets a new file. On a VPS the uploads disk fills up (and PostgreSQL shares it); on S3 every orphan is billed. Services remove the old file with `core.files.delete_file_on_commit`, which deletes it only after the transaction commits:

```python
@transaction.atomic
def document_replace_file(*, document: Document, upload: UploadedFile) -> Document:
    # Takes the old name now; deletes it only if the transaction commits.
    delete_file_on_commit(document.file)
    document.file = upload
    document.full_clean()
    document.save(update_fields=["file"])
    return document


@transaction.atomic
def document_delete(*, document: Document) -> None:
    delete_file_on_commit(document.file)
    document.delete()
```

On rollback nothing is deleted, so a row never points to a missing file. A storage error after the commit is logged as a warning and never fails the request. Keep this in services, not in signals: side effects stay in the service layer. Tests use pytest-django's `django_capture_on_commit_callbacks(execute=True)` to run the deletion.

## S3-compatible storage (optional)

By default uploads stay on the VPS, in the uploads folder, which is the cheapest option. To keep them in a bucket on AWS S3 or an S3-compatible service (Cloudflare R2, DigitalOcean Spaces, Backblaze B2, MinIO, Hetzner Object Storage) without changing code:

1. Build the image with the `s3` extra: set `DOCKER_PROD_UV_EXTRAS=s3` in `docker/prod/.env`, and compose passes it as the `UV_EXTRAS` build arg. The default image carries no boto3 (about 30 MB less).
2. Set `DJANGO_STORAGE_BACKEND=s3`, `DJANGO_AWS_STORAGE_BUCKET_NAME`, `DJANGO_AWS_S3_REGION_NAME` and, for providers other than AWS, `DJANGO_AWS_S3_ENDPOINT_URL`. Credentials go in `AWS_ACCESS_KEY_ID` and `AWS_SECRET_ACCESS_KEY`, which boto3 reads; they are never Django settings.
3. Rebuild and restart: `docker compose -f docker/prod/compose.yaml up -d --build`.

Public files live under `public/` and are served by the bucket, or by a CDN through `DJANGO_AWS_S3_CUSTOM_DOMAIN`. Private files live under `private/`, and `core.files.protected_file_response` redirects to a signed URL valid for `DJANGO_AWS_QUERYSTRING_EXPIRE` seconds (300). Names never collide (`file_overwrite=False`) and objects get no ACL, so access comes only from the bucket policy. That policy must make `public/*` readable and nothing else:

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Sid": "PublicUploadsOnly",
      "Effect": "Allow",
      "Principal": "*",
      "Action": "s3:GetObject",
      "Resource": "arn:aws:s3:::<bucket>/public/*"
    }
  ]
}
```

Without the policy, public URLs return 403. Never make `private/` public: private files rely on signed URLs.

- The nginx headers for `/media/` (`nosniff`, the `sandbox` CSP, attachment for HTML and SVG) do not apply to a bucket. If users can upload types a browser could run, set `ContentDisposition: attachment` and the `ContentType` per object (django-storages' `object_parameters` option or `get_object_parameters()`), or keep those types out with the extension allowlist.
- Moving from the uploads folder: `aws s3 sync /srv/project-name/uploads/public s3://<bucket>/public`, and the same for `private` (add `--endpoint-url` for providers other than AWS), then switch the variables.
- Compose still mounts the uploads folder and `start.sh` still checks that it is writable; with S3 it stays empty.
- `makemigrations --check` shows no change: fields reference the storage alias (`core.files.private_storage`), not the backend.

## Tests

Tests store uploads in memory (`InMemoryStorage`, set in the root `conftest.py`), so they never write to disk.
