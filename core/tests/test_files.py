from __future__ import annotations

import re
from contextlib import suppress
from http import HTTPStatus
from types import SimpleNamespace
from typing import TYPE_CHECKING
from typing import override
from urllib.parse import parse_qs
from urllib.parse import urlencode
from urllib.parse import urlsplit

import pytest
from django.contrib.auth.models import Group
from django.core.exceptions import SuspiciousFileOperation
from django.core.exceptions import ValidationError
from django.core.files.base import ContentFile
from django.core.files.storage import FileSystemStorage
from django.core.files.storage import InMemoryStorage
from django.core.files.storage import default_storage
from django.core.files.uploadedfile import UploadedFile
from django.db import models
from django.db import transaction
from django.db.migrations.writer import MigrationWriter
from django.db.models.fields.files import FieldFile
from django.http import FileResponse
from django.http import HttpResponse
from django.test import override_settings

from config.settings import base as base_settings
from core.files import MaxFileSizeValidator
from core.files import UploadTo
from core.files import delete_file_on_commit
from core.files import private_storage
from core.files import protected_file_response

if TYPE_CHECKING:
    from pathlib import Path

    from django.core.files.storage import Storage
    from pytest_django.fixtures import DjangoCaptureOnCommitCallbacks

GENERATED = re.compile(r"invoices/document/[0-9a-f]{32}(\.[a-z0-9]+)?")


def _instance() -> models.Model:
    # Django always passes the model instance that owns the file. UploadTo and
    # the FieldFile reads here never look at it, so any unsaved instance will do.
    return Group()


@pytest.mark.parametrize(
    ("client_name", "extension"),
    [
        ("../../etc/My Scan.PDF", ".pdf"),
        ("C:\\Users\\ana\\Pasaporte Ana Pérez.jpeg", ".jpeg"),
        ("archive.tar.gz", ".gz"),
        ("a.p h p", ""),
        ("noext", ""),
        ("weird.toolongextension", ""),
    ],
)
def test_upload_to_drops_the_client_name_and_directories(
    client_name: str,
    extension: str,
) -> None:
    name = UploadTo("invoices/document")(_instance(), client_name)

    assert GENERATED.fullmatch(name)
    assert name.endswith(extension) if extension else "." not in name.rsplit("/", 1)[1]
    assert "Scan" not in name
    assert ".." not in name


def test_upload_to_names_are_unique() -> None:
    upload_to = UploadTo("invoices/document")

    names = {upload_to(_instance(), "same.pdf") for _ in range(50)}

    assert len(names) == 50  # noqa: PLR2004


def test_helpers_serialize_into_migrations() -> None:
    upload_to, upload_imports = MigrationWriter.serialize(UploadTo("invoices/document"))
    validator, validator_imports = MigrationWriter.serialize(MaxFileSizeValidator(1024))

    assert upload_to == "core.files.UploadTo('invoices/document')"
    assert validator == "core.files.MaxFileSizeValidator(1024)"
    assert upload_imports == validator_imports == {"import core.files"}
    assert UploadTo("/invoices/document/") == UploadTo("invoices/document")


def test_helpers_compare_and_hash_by_value() -> None:
    # Django asks deconstructible validators for __eq__; hash follows it.
    assert UploadTo("a") == UploadTo("a")
    assert UploadTo("a") != UploadTo("b")
    assert UploadTo("a") != SimpleNamespace(prefix="a")
    assert hash(UploadTo("a")) == hash(UploadTo("a"))
    assert hash(UploadTo("a")) != hash(UploadTo("b"))
    assert MaxFileSizeValidator(10) == MaxFileSizeValidator(10)
    assert MaxFileSizeValidator(10) != MaxFileSizeValidator(11)
    assert MaxFileSizeValidator(10) != SimpleNamespace(max_bytes=10)
    assert hash(MaxFileSizeValidator(10)) == hash(MaxFileSizeValidator(10))
    assert hash(MaxFileSizeValidator(10)) != hash(MaxFileSizeValidator(11))


def test_max_file_size_validator() -> None:
    validator = MaxFileSizeValidator(10)

    validator(ContentFile(b"x" * 10, name="ok.txt"))
    with pytest.raises(ValidationError) as excinfo:
        validator(ContentFile(b"x" * 11, name="big.txt"))

    assert excinfo.value.code == "file_too_large"
    assert excinfo.value.params == {"max_bytes": 10}
    assert excinfo.value.messages == ["Ensure this file is at most 10 bytes."]


def test_tests_store_uploads_in_memory() -> None:
    name = default_storage.save(UploadTo("t")(_instance(), "a.txt"), ContentFile(b"hi"))

    assert default_storage.__class__.__name__ == "InMemoryStorage"
    assert default_storage.open(name).read() == b"hi"


def _field_file(storage: Storage, name: str) -> FieldFile:
    # A FileField on no model: FieldFile only reads field.storage here.
    return FieldFile(_instance(), models.FileField(storage=storage), name)


@override_settings(PRIVATE_MEDIA_ACCEL_REDIRECT_PREFIX="/protected-media/")
def test_local_private_file_is_served_by_nginx_through_x_accel_redirect(
    tmp_path: Path,
) -> None:
    storage = FileSystemStorage(location=tmp_path)
    name = storage.save("invoices/document/abc.pdf", ContentFile(b"%PDF-1.7"))

    response = protected_file_response(
        _field_file(storage, name), filename="Factura.pdf"
    )

    assert response["X-Accel-Redirect"] == "/protected-media/invoices/document/abc.pdf"
    assert response["Content-Disposition"] == 'attachment; filename="Factura.pdf"'
    assert response["Content-Type"] == "application/pdf"
    assert response["X-Content-Type-Options"] == "nosniff"
    assert isinstance(response, HttpResponse)
    assert response.content == b""


@override_settings(PRIVATE_MEDIA_ACCEL_REDIRECT_PREFIX="/protected-media/")
@pytest.mark.parametrize(
    "name",
    [
        "../secret.txt",
        "ok/../../etc/passwd",
        "subdir/../other.txt",
        "ok\\..\\..\\etc\\passwd",
        "invoices/a.pdf\x00.png",
        "invoices/",
        "",
    ],
)
def test_x_accel_redirect_refuses_an_unsafe_stored_name(
    tmp_path: Path, name: str
) -> None:
    # UploadTo never writes these; a name planted in the database must not
    # reach nginx, whose internal location then serves whatever it points at.
    storage = FileSystemStorage(location=tmp_path)

    with pytest.raises(SuspiciousFileOperation):
        protected_file_response(_field_file(storage, name), filename="a.pdf")


@override_settings(PRIVATE_MEDIA_ACCEL_REDIRECT_PREFIX="/protected-media/")
def test_x_accel_redirect_joins_the_prefix_and_the_name_with_one_slash(
    tmp_path: Path,
) -> None:
    storage = FileSystemStorage(location=tmp_path)
    name = storage.save("invoices/a.xyzzy", ContentFile(b"x"))

    response = protected_file_response(_field_file(storage, "/" + name))

    assert response["X-Accel-Redirect"] == "/protected-media/invoices/a.xyzzy"
    # An extension mimetypes does not know is served as plain bytes.
    assert response["Content-Type"] == "application/octet-stream"


@override_settings(PRIVATE_MEDIA_ACCEL_REDIRECT_PREFIX="")
def test_without_a_proxy_django_streams_the_private_file() -> None:
    storage = private_storage()
    name = storage.save("invoices/document/abc.txt", ContentFile(b"secret"))

    response = protected_file_response(_field_file(storage, name))

    assert "X-Accel-Redirect" not in response
    assert response["Content-Disposition"] == 'attachment; filename="abc.txt"'
    # Iterating the response is how Django documents reading a streamed body.
    assert isinstance(response, FileResponse)
    assert b"".join(response) == b"secret"


class _SignedStorage(InMemoryStorage):
    """S3Storage's url(): the signed parameters go in the query string."""

    querystring_auth = True

    @override
    def url(self, name: str | None, parameters: dict[str, str] | None = None) -> str:
        query = urlencode({**(parameters or {}), "X-Amz-Signature": "abc"})
        return f"https://bucket.example.org/{name}?{query}"


def test_signed_url_storages_redirect() -> None:
    response = protected_file_response(_field_file(_SignedStorage(), "invoices/a.pdf"))

    assert response.status_code == HTTPStatus.FOUND
    assert response["Location"].startswith("https://bucket.example.org/invoices/a.pdf?")
    assert "X-Amz-Signature=abc" in response["Location"]


def test_a_signed_url_names_the_download() -> None:
    # The object store serves the file, so the name goes in the signed URL.
    response = protected_file_response(
        _field_file(_SignedStorage(), "invoices/abc123.pdf"), filename="invoice.pdf"
    )
    query = parse_qs(urlsplit(response["Location"]).query)
    assert query["ResponseContentDisposition"] == ['attachment; filename="invoice.pdf"']


def _private_on_disk(tmp_path: Path) -> override_settings:
    """The base settings' private storage (FileSystemStorage), under tmp_path."""
    return override_settings(
        STORAGES={
            **base_settings.STORAGES,
            "private": {
                **base_settings.STORAGES["private"],
                "OPTIONS": {
                    **base_settings.STORAGES["private"]["OPTIONS"],
                    "location": str(tmp_path),
                },
            },
        },
    )


def test_private_storage_follows_storages_overrides(tmp_path: Path) -> None:
    assert isinstance(private_storage(), InMemoryStorage)

    with _private_on_disk(tmp_path):
        storage = private_storage()
        assert isinstance(storage, FileSystemStorage)
        # Private files never get a URL under MEDIA_URL that nginx would serve.
        assert storage.url("a.pdf") == "/private-media-not-served/a.pdf"

    assert isinstance(private_storage(), InMemoryStorage)


def test_a_field_follows_storages_overrides_after_its_storage_was_used(
    tmp_path: Path,
) -> None:
    # Django calls a callable storage once, when the field is built, and the field
    # keeps what it returned: only the lazy proxy follows later overrides.
    field = models.FileField(storage=private_storage, upload_to=UploadTo("x/y"))
    assert isinstance(field.storage, InMemoryStorage)

    with _private_on_disk(tmp_path):
        assert isinstance(field.storage, FileSystemStorage)

    assert isinstance(field.storage, InMemoryStorage)


def test_private_storage_serializes_as_a_reference_in_migrations() -> None:
    field = models.FileField(storage=private_storage, upload_to=UploadTo("x/y"))

    _, _, _, kwargs = field.deconstruct()

    assert (
        MigrationWriter.serialize(kwargs["storage"])[0] == "core.files.private_storage"
    )


class _RollbackError(Exception):
    pass


class _FailingStorage(InMemoryStorage):
    @override
    def delete(self, name: str) -> None:
        msg = f"cannot delete {name}"
        raise OSError(msg)


def _stored(storage: Storage | None = None) -> tuple[FieldFile, str]:
    storage = storage or default_storage
    name = storage.save("uploads/old.txt", ContentFile(b"old"))
    return _field_file(storage, name), name


@pytest.mark.django_db
def test_file_is_deleted_after_commit(
    django_capture_on_commit_callbacks: DjangoCaptureOnCommitCallbacks,
) -> None:
    old, name = _stored()

    with django_capture_on_commit_callbacks(execute=True), transaction.atomic():
        delete_file_on_commit(old)
        assert default_storage.exists(name)

    assert not default_storage.exists(name)


@pytest.mark.django_db
def test_file_survives_a_rollback(
    django_capture_on_commit_callbacks: DjangoCaptureOnCommitCallbacks,
) -> None:
    old, name = _stored()

    with (
        django_capture_on_commit_callbacks(execute=True) as callbacks,
        suppress(_RollbackError),
        transaction.atomic(),
    ):
        delete_file_on_commit(old)
        raise _RollbackError

    assert callbacks == []
    assert default_storage.exists(name)


@pytest.mark.django_db
def test_empty_field_is_a_no_op(
    django_capture_on_commit_callbacks: DjangoCaptureOnCommitCallbacks,
) -> None:
    with django_capture_on_commit_callbacks(execute=True) as callbacks:
        delete_file_on_commit(_field_file(default_storage, ""))

    assert callbacks == []


@pytest.mark.django_db
def test_storage_errors_are_logged_not_raised(
    django_capture_on_commit_callbacks: DjangoCaptureOnCommitCallbacks,
    caplog: pytest.LogCaptureFixture,
) -> None:
    old, _ = _stored(_FailingStorage())

    with django_capture_on_commit_callbacks(execute=True):
        delete_file_on_commit(old)

    assert "Could not delete stored file uploads/old" in caplog.text


def test_a_file_of_unknown_size_is_not_refused() -> None:
    # Django's upload handlers always set a size; only a hand-built file may
    # have none, and the validator lets it through rather than guess.
    MaxFileSizeValidator(10)(UploadedFile(name="x.bin"))
