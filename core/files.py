"""
Upload helpers: generated file names, a size limit and private storage.

Every FileField/ImageField uses `UploadTo("<app>/<field>")` and
`MaxFileSizeValidator` next to an extension allow-list; both are deconstructible.
See docs/files.md#field.
"""

from __future__ import annotations

import logging
import mimetypes
import re
import uuid
from functools import partial
from pathlib import PurePosixPath
from typing import TYPE_CHECKING
from typing import Any

from django.conf import settings
from django.core.exceptions import SuspiciousFileOperation
from django.core.exceptions import ValidationError
from django.core.files.storage import FileSystemStorage
from django.core.files.storage import storages
from django.core.files.utils import validate_file_name
from django.core.signals import setting_changed
from django.db import transaction
from django.dispatch import receiver
from django.http import FileResponse
from django.http import HttpResponse
from django.http import HttpResponseRedirect
from django.utils.deconstruct import deconstructible
from django.utils.functional import LazyObject
from django.utils.functional import empty
from django.utils.http import content_disposition_header

if TYPE_CHECKING:
    from django.core.files import File
    from django.core.files.storage import Storage
    from django.db.models import Model
    from django.db.models.fields.files import FieldFile
    from django.http.response import HttpResponseBase

logger = logging.getLogger(__name__)

_SAFE_EXTENSION = re.compile(r"\.[a-z0-9]{1,10}")


@deconstructible
class UploadTo:
    """`upload_to` callable: `<prefix>/<uuid hex><.ext>`; the client name is dropped."""

    def __init__(self, prefix: str) -> None:
        self.prefix = prefix.strip("/")

    def __call__(self, instance: Model, filename: str) -> str:
        extension = PurePosixPath(filename.replace("\\", "/")).suffix.lower()
        if not _SAFE_EXTENSION.fullmatch(extension):
            extension = ""
        return f"{self.prefix}/{uuid.uuid4().hex}{extension}"

    def __eq__(self, other: object) -> bool:
        return isinstance(other, UploadTo) and other.prefix == self.prefix

    def __hash__(self) -> int:
        return hash(self.prefix)


@deconstructible
class MaxFileSizeValidator:
    """Reject files larger than `max_bytes` (Django has no built-in size validator)."""

    message = "Ensure this file is at most %(max_bytes)s bytes."
    code = "file_too_large"

    def __init__(self, max_bytes: int) -> None:
        self.max_bytes = max_bytes

    def __call__(self, file: File) -> None:
        if file.size is not None and file.size > self.max_bytes:
            raise ValidationError(
                self.message,
                code=self.code,
                params={"max_bytes": self.max_bytes},
            )

    def __eq__(self, other: object) -> bool:
        return (
            isinstance(other, MaxFileSizeValidator)
            and other.max_bytes == self.max_bytes
        )

    def __hash__(self) -> int:
        return hash(self.max_bytes)


class _PrivateStorage(LazyObject):
    """STORAGES["private"], resolved on first use and again after a settings change."""

    _wrapped: Any

    def _setup(self) -> None:
        self._wrapped = storages["private"]

    def reset(self) -> None:
        self._wrapped = empty


_private_storage = _PrivateStorage()


@receiver(setting_changed)
def _reset_private_storage(*, setting: str, **kwargs: Any) -> None:
    if setting == "STORAGES":
        _private_storage.reset()


def private_storage() -> Any:
    """
    `storage=` for files only their owner may download.

    A callable that returns a lazy proxy: migrations never bake in the backend, and
    STORAGES overrides reach fields. See docs/files.md#public-or-private.
    """
    return _private_storage


def protected_file_response(
    field_file: FieldFile,
    *,
    filename: str | None = None,
) -> HttpResponseBase:
    """
    Return the file to a user the view has already authorized.

    A signed-URL redirect on S3, X-Accel-Redirect behind nginx, or a Django stream
    in development. See docs/files.md#public-or-private.
    """
    storage = field_file.storage
    download_name = filename or PurePosixPath(field_file.name or "").name
    if getattr(storage, "querystring_auth", False):
        return HttpResponseRedirect(field_file.url)
    prefix = settings.PRIVATE_MEDIA_ACCEL_REDIRECT_PREFIX
    if prefix and isinstance(storage, FileSystemStorage):
        content_type, _ = mimetypes.guess_type(download_name)
        response = HttpResponse(content_type=content_type or "application/octet-stream")
        # UploadTo never writes such a name, but one planted in the database
        # must not reach nginx: no "..", no NUL, no empty file name. For a path
        # that escapes the storage, storage.open() raises the same error.
        name = (field_file.name or "").lstrip("/")
        if "\x00" in name:
            msg = f"Null byte in the stored file name {name!r}"
            raise SuspiciousFileOperation(msg)
        validate_file_name(name, allow_relative_path=True)
        response["X-Accel-Redirect"] = f"{prefix.rstrip('/')}/{name}"
        response["Content-Disposition"] = content_disposition_header(
            as_attachment=True,
            filename=download_name,
        )
        response["X-Content-Type-Options"] = "nosniff"
        return response
    return FileResponse(
        field_file.open("rb"), as_attachment=True, filename=download_name
    )


def delete_file_on_commit(field_file: FieldFile) -> None:
    """
    Delete a stored file once the current transaction commits.

    Call it with the old file before replacing or deleting it: nothing is deleted on
    rollback, and a storage error after the commit is only logged.
    See docs/files.md#replacing-and-deleting-files.
    """
    if not field_file.name:
        return
    # Capture storage and name now: the field may hold another file by commit time.
    delete = partial(_delete_quietly, field_file.storage, field_file.name)
    transaction.on_commit(delete, robust=True)


def _delete_quietly(storage: Storage, name: str) -> None:
    try:
        storage.delete(name)
    # Storage backends raise anything (OSError, botocore errors); the row is
    # already committed, so a leftover file is only logged.
    except Exception:
        logger.warning("Could not delete stored file %s", name, exc_info=True)
