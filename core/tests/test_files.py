from __future__ import annotations

import re
from contextlib import suppress
from http import HTTPStatus
from types import SimpleNamespace
from typing import TYPE_CHECKING
from typing import Any
from typing import cast

import pytest
from django.core.exceptions import SuspiciousFileOperation
from django.core.exceptions import ValidationError
from django.core.files.base import ContentFile
from django.core.files.storage import FileSystemStorage
from django.core.files.storage import InMemoryStorage
from django.core.files.storage import default_storage
from django.db import models
from django.db import transaction
from django.db.migrations.writer import MigrationWriter
from django.db.models.fields.files import FieldFile
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

    from pytest_django.fixtures import DjangoCaptureOnCommitCallbacks

GENERATED = re.compile(r"invoices/document/[0-9a-f]{32}(\.[a-z0-9]+)?")


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
    name = UploadTo("invoices/document")(None, client_name)  # type: ignore[arg-type]  # pyright: ignore[reportArgumentType]

    assert GENERATED.fullmatch(name)
    assert name.endswith(extension) if extension else "." not in name.rsplit("/", 1)[1]
    assert "Scan" not in name
    assert ".." not in name


def test_upload_to_names_are_unique() -> None:
    upload_to = UploadTo("invoices/document")

    names = {upload_to(None, "same.pdf") for _ in range(50)}  # type: ignore[arg-type]  # pyright: ignore[reportArgumentType]

    assert len(names) == 50  # noqa: PLR2004


def test_helpers_serialize_into_migrations() -> None:
    upload_to, upload_imports = MigrationWriter.serialize(UploadTo("invoices/document"))
    validator, validator_imports = MigrationWriter.serialize(MaxFileSizeValidator(1024))

    assert upload_to == "core.files.UploadTo('invoices/document')"
    assert validator == "core.files.MaxFileSizeValidator(1024)"
    assert upload_imports == validator_imports == {"import core.files"}
    assert UploadTo("/invoices/document/") == UploadTo("invoices/document")


def test_max_file_size_validator() -> None:
    validator = MaxFileSizeValidator(10)

    validator(ContentFile(b"x" * 10, name="ok.txt"))
    with pytest.raises(ValidationError) as excinfo:
        validator(ContentFile(b"x" * 11, name="big.txt"))

    assert excinfo.value.code == "file_too_large"
    assert excinfo.value.params == {"max_bytes": 10}


def test_tests_store_uploads_in_memory() -> None:
    name = default_storage.save(UploadTo("t")(None, "a.txt"), ContentFile(b"hi"))  # type: ignore[arg-type]  # pyright: ignore[reportArgumentType]

    assert default_storage.__class__.__name__ == "InMemoryStorage"
    assert default_storage.open(name).read() == b"hi"


def _field_file(storage: Any, name: str) -> FieldFile:
    # FieldFile only needs field.storage to open the file.
    return FieldFile(None, SimpleNamespace(storage=storage), name)  # type: ignore[arg-type]  # pyright: ignore[reportArgumentType]


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


def test_without_a_proxy_django_streams_the_private_file() -> None:
    storage = private_storage()
    name = storage.save("invoices/document/abc.txt", ContentFile(b"secret"))

    response = protected_file_response(_field_file(storage, name))

    assert response["Content-Disposition"] == 'attachment; filename="abc.txt"'
    assert b"".join(response.streaming_content) == b"secret"  # type: ignore[attr-defined]  # pyright: ignore[reportAttributeAccessIssue]


def test_signed_url_storages_redirect() -> None:
    class SignedStorage(InMemoryStorage):
        querystring_auth = True

        def url(self, name: str | None) -> str:
            return f"https://bucket.example.org/{name}?X-Amz-Signature=abc"

    response = protected_file_response(_field_file(SignedStorage(), "invoices/a.pdf"))

    assert response.status_code == HTTPStatus.FOUND
    assert response["Location"].endswith("invoices/a.pdf?X-Amz-Signature=abc")


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
    def delete(self, name: str) -> None:
        msg = f"cannot delete {name}"
        raise OSError(msg)


def _stored(storage: Any = None) -> tuple[FieldFile, str]:
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
    # Django's upload handlers always set a size; only a hand-built File may
    # have none, and the validator lets it through rather than guess.
    MaxFileSizeValidator(10)(cast("Any", SimpleNamespace(size=None, name="x.bin")))
