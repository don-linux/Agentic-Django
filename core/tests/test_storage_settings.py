from __future__ import annotations

import subprocess
from http import HTTPStatus
from typing import TYPE_CHECKING

import pytest
from django.contrib.auth.models import Group
from django.core.files.storage import storages
from django.db import models
from django.db.models.fields.files import FieldFile

from core.files import protected_file_response
from core.tests.production_settings import load_production_settings
from core.tests.production_settings import run_python

if TYPE_CHECKING:
    from pytest_django.fixtures import Settings

S3_ENV = {
    "DJANGO_STORAGE_BACKEND": "s3",
    "DJANGO_AWS_STORAGE_BUCKET_NAME": "media-bucket",
    "DJANGO_AWS_S3_REGION_NAME": "us-east-1",
    "DJANGO_AWS_S3_CUSTOM_DOMAIN": "cdn.example.invalid",
}
S3 = "storages.backends.s3.S3Storage"
WITHOUT_THE_EXTRA = (
    "import importlib.util\n"
    "real = importlib.util.find_spec\n"
    "importlib.util.find_spec = lambda name, *a, **k: (\n"
    "    None if name == 'storages' else real(name, *a, **k)\n"
    ")\n"
    "import config.settings.production\n"
)


def _storages(env: dict[str, str] | None = None) -> dict:
    return load_production_settings("STORAGES", env=env)["STORAGES"]


def test_local_filesystem_is_the_default() -> None:
    configured = _storages()

    backends = {configured["default"]["BACKEND"], configured["private"]["BACKEND"]}
    assert S3 not in backends
    assert configured["staticfiles"]["BACKEND"].startswith("whitenoise.")


def test_s3_backend_is_selected_by_environment() -> None:
    configured = _storages(S3_ENV)
    public, private = configured["default"], configured["private"]

    assert public["BACKEND"] == private["BACKEND"] == S3
    common = {
        "bucket_name": "media-bucket",
        "region_name": "us-east-1",
        "endpoint_url": None,
        "file_overwrite": False,
        "default_acl": None,
        "signature_version": "s3v4",
    }
    assert public["OPTIONS"] == {
        **common,
        "location": "public",
        "querystring_auth": False,
        "custom_domain": "cdn.example.invalid",
    }
    assert private["OPTIONS"] == {
        **common,
        "location": "private",
        "querystring_auth": True,
        "querystring_expire": 300,
    }
    assert configured["staticfiles"]["BACKEND"].startswith("whitenoise.")


def test_unknown_storage_backend_is_refused() -> None:
    with pytest.raises(subprocess.CalledProcessError) as excinfo:
        _storages({"DJANGO_STORAGE_BACKEND": "gcs"})

    stderr = excinfo.value.stderr
    assert "DJANGO_STORAGE_BACKEND: Input should be 'local' or 's3'" in stderr
    # The message names the variable, never its value.
    assert "gcs" not in stderr


def test_s3_without_a_bucket_is_refused() -> None:
    with pytest.raises(subprocess.CalledProcessError) as excinfo:
        load_production_settings(
            "STORAGES", env={**S3_ENV, "DJANGO_AWS_STORAGE_BUCKET_NAME": None}
        )

    assert (
        "Set the DJANGO_AWS_STORAGE_BUCKET_NAME environment variable."
        in excinfo.value.stderr
    )


def test_s3_without_the_image_extra_is_refused() -> None:
    with pytest.raises(subprocess.CalledProcessError) as excinfo:
        run_python(WITHOUT_THE_EXTRA, env=S3_ENV)

    assert "DOCKER_PROD_UV_EXTRAS=s3" in excinfo.value.stderr


def test_s3_storages_build_public_and_signed_urls(
    settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The production options, used in-process: no request leaves the machine
    # (botocore signs URLs locally).
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "tests-access-key")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "tests-secret-key")
    configured = _storages(S3_ENV)
    settings.STORAGES = {
        **settings.STORAGES,
        "default": configured["default"],
        "private": configured["private"],
    }

    assert (
        storages["default"].url("a.png") == "https://cdn.example.invalid/public/a.png"
    )
    signed = storages["private"].url("b.pdf")
    assert "/private/b.pdf?" in signed
    assert "X-Amz-Expires=300" in signed
    assert "X-Amz-Signature=" in signed

    field = models.FileField(storage=storages["private"])
    # FieldFile needs its owner instance; this read never looks at it.
    response = protected_file_response(FieldFile(Group(), field, "b.pdf"))
    assert response.status_code == HTTPStatus.FOUND
    assert "X-Amz-Signature=" in response["Location"]
