"""Pins for behaviour mutation testing found unguarded in core."""

from __future__ import annotations

import json
import logging
import re
from datetime import timedelta
from http import HTTPStatus
from types import SimpleNamespace
from typing import TYPE_CHECKING
from typing import Any
from typing import cast
from unittest import mock
from uuid import UUID

import pytest
import sentry_sdk
from django.apps.registry import Apps
from django.contrib.auth.hashers import make_password
from django.core.cache import caches
from django.core.exceptions import ImproperlyConfigured
from django.core.exceptions import ValidationError
from django.core.files.base import ContentFile
from django.core.files.storage import FileSystemStorage
from django.db import connection
from django.db import models
from django.db.models.fields.files import FieldFile
from django.db.utils import OperationalError
from django.test import Client
from django.test import RequestFactory
from django.test import override_settings
from django.urls import NoReverseMatch
from django.utils import timezone
from django_stubs_ext.db.models import TypedModelMeta
from redis.exceptions import ConnectionError as ValkeyConnectionError
from sentry_sdk.integrations.logging import LoggingIntegration

from config.settings.guards import MIN_SECRET_KEY_LENGTH
from config.settings.guards import MIN_SECRET_KEY_UNIQUE_CHARACTERS
from config.settings.guards import assert_production_email
from config.settings.guards import assert_production_secrets
from core import sentry as sentry_mod
from core.admin_lockout import is_outside_admin
from core.cache import _MAX_PARTS_LENGTH
from core.cache import NAMESPACE_TTL
from core.cache import build_key
from core.cache import invalidate
from core.cache import read_through
from core.cache_backend import DEFAULT_MAX_TTL  # noqa: F401
from core.cache_backend import ValkeyCache
from core.files import MaxFileSizeValidator
from core.files import UploadTo
from core.files import protected_file_response
from core.logging import JsonFormatter
from core.logging import RequestIdFilter
from core.models import BaseModel
from core.net import get_client_ip
from core.sentry import scrub_event
from core.tests.guard_values import FAKE_DSN
from core.tests.guard_values import STRONG_KEY
from core.tests.guard_values import VALID_SECRETS as SECRET_VALID

if TYPE_CHECKING:
    from pathlib import Path

    from pytest_django.fixtures import DjangoCaptureOnCommitCallbacks

    from conftest import TrustedProxies

NAMESPACE = "tests.thing"
ONE_MINUTE = 60

MAX_PARTS_LENGTH = 160
ROWS = 3


class _Loader:
    def __init__(self, value: object) -> None:
        self.value = value
        self.calls = 0

    def __call__(self) -> object:
        self.calls += 1
        return self.value


def test_a_weak_secret_key_is_rejected_even_with_a_valid_fallback() -> None:
    with pytest.raises(ImproperlyConfigured, match="DJANGO_SECRET_KEY"):
        assert_production_secrets(
            **{
                **SECRET_VALID,
                "secret_key": "change-me",
                "secret_key_fallbacks": [STRONG_KEY[::-1]],
            }
        )


def test_a_secret_key_of_exact_minimum_length_is_accepted() -> None:
    # 50 characters, 5 unique (A, b, 1, -, c). Kills `<` flipped to `<=`.
    key = ("Ab1-c" * (MIN_SECRET_KEY_LENGTH // 5))[:MIN_SECRET_KEY_LENGTH]
    assert len(key) == MIN_SECRET_KEY_LENGTH
    assert len(set(key)) >= MIN_SECRET_KEY_UNIQUE_CHARACTERS
    assert_production_secrets(**{**SECRET_VALID, "secret_key": key})


def test_assert_production_email_with_empty_mailers_is_improperly_configured() -> None:
    with pytest.raises(ImproperlyConfigured):
        assert_production_email(
            mailers={},
            default_from_email="Acme <noreply@acme.invalid>",
            frontend_url="https://app.acme.invalid",
        )


def test_init_sentry_asserts_dsn_hooks_sample_rate_and_log_levels(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, Any] = {}
    monkeypatch.setattr(sentry_sdk, "init", lambda **kw: captured.update(kw))
    logging_kwargs: dict[str, Any] = {}

    def logging_integration(**kwargs: Any) -> Any:
        logging_kwargs.update(kwargs)
        return LoggingIntegration(**kwargs)

    monkeypatch.setattr(sentry_mod, "LoggingIntegration", logging_integration)
    sentry_mod.init_sentry(
        dsn=FAKE_DSN,
        environment="tests",
        release="abc123",
        traces_sample_rate=0.0,
    )
    assert captured["dsn"] == FAKE_DSN
    assert captured["environment"] == "tests"
    assert captured["release"] == "abc123"
    assert captured["traces_sample_rate"] == 0.0
    assert captured["before_send"] is scrub_event
    assert captured["before_send_transaction"] is scrub_event
    assert logging_kwargs == {"level": logging.INFO, "event_level": logging.ERROR}


def test_scrub_event_tolerates_a_request_without_cookies() -> None:
    event = {"request": {"headers": {"Accept": "application/json"}}}
    assert scrub_event(event, {})["request"] == {
        "headers": {"Accept": "application/json"}
    }


@pytest.mark.django_db
def test_a_one_second_ttl_is_accepted(
    django_capture_on_commit_callbacks: DjangoCaptureOnCommitCallbacks,
) -> None:
    loader = _Loader("value")
    with django_capture_on_commit_callbacks(execute=True):
        read_through(NAMESPACE, 1, ttl=1, loader=loader)
    assert read_through(NAMESPACE, 1, ttl=1, loader=loader) == "value"
    assert loader.calls == 1


@pytest.mark.django_db
def test_a_tail_of_exactly_max_length_is_not_hashed() -> None:
    # A single part of length 160 is hashed by `_part` (max 64). The 160 bound
    # is on the joined tail of already-safe parts.
    assert _MAX_PARTS_LENGTH == MAX_PARTS_LENGTH
    parts = ("a" * 64, "b" * 64, "c" * 30)
    tail = ":".join(parts)
    assert len(tail) == _MAX_PARTS_LENGTH
    key = build_key(NAMESPACE, *parts)
    assert key.endswith(f":{tail}")


@pytest.mark.django_db
def test_a_cached_read_stores_the_given_ttl(
    monkeypatch: pytest.MonkeyPatch,
    django_capture_on_commit_callbacks: DjangoCaptureOnCommitCallbacks,
) -> None:
    stored: list[int | None] = []
    real_set = caches["default"].set

    def set_(key, value, timeout=None, *args, **kwargs):
        stored.append(timeout)
        return real_set(key, value, timeout, *args, **kwargs)

    monkeypatch.setattr(caches["default"], "set", set_)
    with django_capture_on_commit_callbacks(execute=True):
        read_through(NAMESPACE, 1, ttl=ONE_MINUTE, loader=_Loader("value"))
    assert stored == [ONE_MINUTE]


@pytest.mark.django_db
def test_a_failing_cache_read_warning_keeps_the_traceback(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    def broken_get(*_a, **_k):
        msg = "unpickle failed"
        raise RuntimeError(msg)

    monkeypatch.setattr(caches["default"], "get", broken_get)
    with caplog.at_level(logging.WARNING, logger="core.cache"):
        assert (
            read_through(NAMESPACE, 1, ttl=ONE_MINUTE, loader=_Loader("value"))
            == "value"
        )
    assert caplog.records
    # Must be a real sys.exc_info() tuple. `exc_info=False` stores False,
    # which is not None and would slip past a naive `is not None` check.
    exc_info = caplog.records[0].exc_info
    assert exc_info
    assert exc_info[0] is not None


@pytest.mark.django_db
def test_a_failing_store_after_commit_does_not_fail_the_request(
    monkeypatch: pytest.MonkeyPatch,
    django_capture_on_commit_callbacks: DjangoCaptureOnCommitCallbacks,
) -> None:
    def broken_set(*_a, **_k):
        msg = "cannot pickle this value"
        raise RuntimeError(msg)

    monkeypatch.setattr(caches["default"], "set", broken_set)
    with django_capture_on_commit_callbacks(execute=True):
        assert (
            read_through(NAMESPACE, 1, ttl=ONE_MINUTE, loader=_Loader("value"))
            == "value"
        )


@pytest.mark.django_db
def test_a_missing_version_after_add_uses_the_candidate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    backend = caches["default"]
    monkeypatch.setattr(backend, "add", lambda *a, **k: True)

    def always_miss(key, default=None, *args, **kwargs):
        return default

    monkeypatch.setattr(backend, "get", always_miss)
    assert re.fullmatch(r"tests\.thing:v\d+:1", build_key(NAMESPACE, 1))


@pytest.mark.django_db
def test_invalidate_on_valkey_writes_a_fresh_version_with_namespace_ttl(
    monkeypatch: pytest.MonkeyPatch,
    django_capture_on_commit_callbacks: DjangoCaptureOnCommitCallbacks,
) -> None:
    backend = mock.Mock(spec=ValkeyCache)
    monkeypatch.setattr("core.cache._backend", lambda: backend)
    with django_capture_on_commit_callbacks(execute=True):
        invalidate(NAMESPACE)
        invalidate(NAMESPACE)
    assert backend.strict_set.call_count == len([NAMESPACE, NAMESPACE])
    first_version = backend.strict_set.call_args_list[0].args[1]
    second_version = backend.strict_set.call_args_list[1].args[1]
    assert first_version != second_version
    assert first_version is not None
    assert second_version is not None
    assert backend.strict_set.call_args_list[0].args[2] == NAMESPACE_TTL
    assert backend.strict_set.call_args_list[1].args[2] == NAMESPACE_TTL


def test_client_ip_is_empty_when_remote_addr_is_missing(
    trusted_proxies: TrustedProxies,
) -> None:
    request = RequestFactory().get("/")
    request.META.pop("REMOTE_ADDR", None)
    with trusted_proxies(0):
        assert get_client_ip(request) == ""


def test_request_id_filter_must_not_drop_records() -> None:
    record = logging.LogRecord(
        name="tests.logging",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg="hello",
        args=(),
        exc_info=None,
    )
    assert RequestIdFilter().filter(record) is True


def _log_record(**extra: Any) -> logging.LogRecord:
    record = logging.LogRecord(
        name=extra.pop("name", "tests.logging"),
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg="hello %s",
        args=("world",),
        exc_info=None,
    )
    for key, value in extra.items():
        setattr(record, key, value)
    return record


def test_json_formatter_includes_process_and_stack_info() -> None:
    record = _log_record()
    record.stack_info = "stack"
    entry = json.loads(JsonFormatter().format(record))
    assert entry["process"] == record.process
    assert entry["stack_info"] == "stack"


def test_json_formatter_stringifies_non_json_extras() -> None:
    entry = json.loads(JsonFormatter().format(_log_record(id=UUID(int=0))))
    assert entry["id"] == "00000000-0000-0000-0000-000000000000"


def test_celery_logger_keeps_extras_other_than_data() -> None:
    record = _log_record(data={"kwargs": "{}"}, rows=ROWS)
    record.name = "celery.app.trace"
    entry = json.loads(JsonFormatter().format(record))
    assert "data" not in entry
    assert entry["rows"] == ROWS


def _sample_model_with_note() -> Any:
    isolated_apps = Apps(["core"])

    class SampleItem(BaseModel):
        title = models.CharField(max_length=100)
        note = models.CharField(max_length=100, default="original")

        class Meta(BaseModel.Meta, TypedModelMeta):
            app_label = "core"
            apps = isolated_apps

    return SampleItem


@pytest.mark.django_db
def test_save_with_update_fields_does_not_write_other_dirty_fields() -> None:
    sample_model = _sample_model_with_note()
    with connection.schema_editor() as schema_editor:
        schema_editor.create_model(sample_model)

    item = sample_model.objects.create(title="Initial", note="original")
    past = timezone.now() - timedelta(hours=1)
    sample_model.objects.filter(pk=item.pk).update(updated_at=past)
    item.refresh_from_db()

    item.title = "Renamed"
    item.note = "should stay"
    item.save(update_fields=["title"])
    item.refresh_from_db()
    assert item.title == "Renamed"
    assert item.note == "original"
    assert item.updated_at > past


def test_upload_to_and_size_validator_eq_and_hash() -> None:
    assert UploadTo("a") == UploadTo("a")
    assert UploadTo("a") != UploadTo("b")
    assert UploadTo("a") != SimpleNamespace(prefix="a")
    assert hash(UploadTo("a")) == hash(UploadTo("a"))
    assert hash(UploadTo("a")) != hash(UploadTo("b"))
    assert hash(UploadTo("a")) == hash("a")
    assert MaxFileSizeValidator(10) == MaxFileSizeValidator(10)
    assert MaxFileSizeValidator(10) != MaxFileSizeValidator(11)
    assert MaxFileSizeValidator(10) != SimpleNamespace(max_bytes=10)
    assert hash(MaxFileSizeValidator(10)) == hash(MaxFileSizeValidator(10))
    assert hash(MaxFileSizeValidator(10)) != hash(MaxFileSizeValidator(11))
    assert hash(MaxFileSizeValidator(10)) == hash(10)


def _field_file(storage: Any, name: str) -> Any:
    field = cast("Any", SimpleNamespace(storage=storage))
    return FieldFile(cast("Any", None), field, name)


@override_settings(PRIVATE_MEDIA_ACCEL_REDIRECT_PREFIX="")
def test_empty_accel_prefix_streams_from_django(tmp_path: Path) -> None:
    storage = FileSystemStorage(location=tmp_path)
    name = storage.save("invoices/a.bin", ContentFile(b"x"))
    response = protected_file_response(_field_file(storage, name))
    assert "X-Accel-Redirect" not in response
    assert b"".join(cast("Any", response).streaming_content) == b"x"
    stream = getattr(response, "file_to_stream", None)
    if stream is not None:
        stream.close()


@override_settings(PRIVATE_MEDIA_ACCEL_REDIRECT_PREFIX="/protected-media/")
def test_accel_redirect_strips_slashes_and_defaults_octet_stream(
    tmp_path: Path,
) -> None:
    storage = FileSystemStorage(location=tmp_path)
    name = storage.save("invoices/a.xyzzy", ContentFile(b"x"))
    response = protected_file_response(_field_file(storage, "/" + name))
    assert response["X-Accel-Redirect"] == "/protected-media/invoices/a.xyzzy"
    assert response["Content-Type"] == "application/octet-stream"


@pytest.mark.django_db
def test_health_and_liveness_are_text_plain_and_post_allows_get(client: Client) -> None:
    for path in ("/health/", "/health/live/"):
        response = client.get(path)
        assert response.status_code == HTTPStatus.OK
        assert response.headers["Content-Type"].startswith("text/plain")
        denied = client.post(path)
        assert denied.status_code == HTTPStatus.METHOD_NOT_ALLOWED
        assert denied.headers["Allow"] == "GET"


@pytest.mark.django_db
def test_health_database_unavailable_is_logged(
    client: Client,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    def fail_cursor(*_args: object, **_kwargs: object) -> None:
        msg = "could not connect to server at postgres_db"
        raise OperationalError(msg)

    monkeypatch.setattr("core.middleware.connection.cursor", fail_cursor)
    with caplog.at_level(logging.WARNING, logger="core.middleware"):
        response = client.get("/health/")
    assert response.status_code == HTTPStatus.SERVICE_UNAVAILABLE
    records = [r for r in caplog.records if r.name == "core.middleware"]
    assert records
    assert records[0].getMessage() == "healthcheck database unavailable"
    exc_info = records[0].exc_info
    assert exc_info
    assert exc_info[0] is not None
    assert "password" not in caplog.text.lower()


@pytest.mark.django_db
def test_health_cache_unavailable_is_logged(
    client: Client,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    def fail_probe() -> None:
        msg = "Error 111 connecting to valkey:6379. Connection refused."
        raise ValkeyConnectionError(msg)

    monkeypatch.setattr("core.middleware.probe", fail_probe)
    with caplog.at_level(logging.WARNING, logger="core.middleware"):
        response = client.get("/health/")
    assert response.status_code == HTTPStatus.SERVICE_UNAVAILABLE
    records = [r for r in caplog.records if r.name == "core.middleware"]
    assert records
    assert records[0].getMessage() == "healthcheck cache unavailable"
    exc_info = records[0].exc_info
    assert exc_info
    assert exc_info[0] is not None
    assert "password" not in caplog.text.lower()


def test_is_outside_admin_when_admin_is_not_mounted(
    rf: RequestFactory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def _missing(*_a, **_k):
        raise NoReverseMatch

    monkeypatch.setattr("core.admin_lockout.reverse", _missing)
    assert is_outside_admin(rf.post("/api/v1/auth/login/"), {}) is True


def test_max_file_size_validator_message() -> None:
    with pytest.raises(ValidationError) as excinfo:
        MaxFileSizeValidator(10)(ContentFile(b"x" * 11, name="a.bin"))
    assert str(excinfo.value.messages[0]) == "Ensure this file is at most 10 bytes."


# unused import guard (make_password was a leftover thought)
del make_password
