from __future__ import annotations

import subprocess

import pytest
from celery.contrib.django.task import DjangoTask
from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from kombu import Connection

from config import celery_app
from config.settings.base import build_broker_url
from config.settings.base import check_task_time_limits
from core.tests.production_settings import load_settings
from core.tests.production_settings import run_python

# Long enough for the production guard (config/settings/guards.py).
VALKEY_SECRET = "tests-only-valkey-secret-never-used-anywhere"  # noqa: S105
SERVER = "redis://cache.tests.invalid:6379"
HOST = "cache.tests.invalid"
BROKER_DB = 1
ONE_HOUR = 3600
SETTINGS_MODULES = [
    "config.settings.base",
    "config.settings.local",
    "config.settings.production",
]


def _broker_url(*, username: str = "", password: str = VALKEY_SECRET) -> str:
    return build_broker_url(
        url=SERVER,
        db=BROKER_DB,
        username=username,
        password=password,
    )


def test_the_broker_url_carries_the_database_and_the_password() -> None:
    connection = Connection(_broker_url())

    assert connection.transport_cls == "redis"
    assert connection.hostname == HOST
    assert connection.virtual_host == str(BROKER_DB)
    assert connection.password == VALKEY_SECRET


def test_the_broker_url_has_no_credentials_when_there_are_none() -> None:
    assert _broker_url(password="") == f"{SERVER}/{BROKER_DB}"


def test_the_broker_url_takes_an_acl_username() -> None:
    connection = Connection(_broker_url(username="queue-user"))

    assert connection.userid == "queue-user"
    assert connection.password == VALKEY_SECRET


def test_a_password_with_reserved_characters_survives_the_url() -> None:
    awkward = "p@ss:w/rd?#%&+ and spaces"

    connection = Connection(_broker_url(password=awkward))

    assert connection.password == awkward
    assert connection.hostname == HOST
    assert connection.virtual_host == str(BROKER_DB)


def test_tls_and_its_options_are_kept() -> None:
    url = build_broker_url(
        url="rediss://cache.tests.invalid:6380?ssl_cert_reqs=required",
        db=BROKER_DB,
        username="",
        password=VALKEY_SECRET,
    )

    assert url.startswith("rediss://")
    assert url.endswith(f"/{BROKER_DB}?ssl_cert_reqs=required")


@pytest.mark.parametrize("module", SETTINGS_MODULES)
def test_the_broker_is_database_one_of_the_same_valkey(module: str) -> None:
    broker_url = load_settings(
        module,
        "CELERY_BROKER_URL",
        env={"VALKEY_URL": SERVER, "VALKEY_PASSWORD": VALKEY_SECRET},
    )["CELERY_BROKER_URL"]

    assert broker_url == f"redis://:{VALKEY_SECRET}@{HOST}:6379/{BROKER_DB}"


def test_an_explicit_broker_url_replaces_the_built_one() -> None:
    elsewhere = "rediss://broker.tests.invalid:6380/4"

    broker_url = load_settings(
        "config.settings.base",
        "CELERY_BROKER_URL",
        env={"CELERY_BROKER_URL": elsewhere},
    )["CELERY_BROKER_URL"]

    assert broker_url == elsewhere


def test_the_app_uses_the_django_integration() -> None:
    # Installed only when DJANGO_SETTINGS_MODULE is set before the app is built.
    assert issubclass(celery_app.Task, DjangoTask)


def test_importing_the_package_keeps_production_as_the_default_settings() -> None:
    # config/__init__.py runs before config/wsgi.py: the two defaults must agree,
    # or a bare `gunicorn config.wsgi` would start with other settings.
    result = run_python(
        "import os\nimport config\nprint(os.environ['DJANGO_SETTINGS_MODULE'])\n",
    )

    assert result.stdout.strip() == "config.settings.production"


def test_no_result_backend_and_json_only() -> None:
    conf = celery_app.conf

    assert conf.task_ignore_result is True
    assert conf.result_backend is None
    assert conf.task_serializer == "json"
    assert list(conf.accept_content) == ["json"]
    assert conf.enable_utc is True
    assert conf.timezone == settings.TIME_ZONE


def test_a_lost_worker_does_not_lose_or_loop_a_task() -> None:
    conf = celery_app.conf

    assert conf.task_acks_late is True
    assert conf.task_retry_on_worker_lost is True
    assert not conf.task_reject_on_worker_lost
    assert conf.worker_prefetch_multiplier == 1


def test_time_limits_are_below_the_visibility_timeout() -> None:
    conf = celery_app.conf
    visibility_timeout = conf.broker_transport_options["visibility_timeout"]

    # Otherwise a task that is still running would be delivered to another worker.
    assert 0 < conf.task_soft_time_limit < conf.task_time_limit < visibility_timeout
    assert visibility_timeout == conf.visibility_timeout == ONE_HOUR


@pytest.mark.parametrize(
    ("soft", "hard"),
    [(1800, 1800), (1900, 1800), (1500, 3600), (1500, 4000), (0, 1800)],
)
def test_limits_that_would_redeliver_a_running_task_are_refused(
    soft: int,
    hard: int,
) -> None:
    with pytest.raises(ImproperlyConfigured, match="CELERY_TASK_TIME_LIMIT"):
        check_task_time_limits(soft=soft, hard=hard, visibility_timeout=ONE_HOUR)


def test_a_time_limit_from_the_environment_is_checked_at_import() -> None:
    with pytest.raises(subprocess.CalledProcessError) as excinfo:
        load_settings(
            "config.settings.base",
            "CELERY_TASK_TIME_LIMIT",
            env={"CELERY_TASK_TIME_LIMIT": "7200"},
        )

    assert "ImproperlyConfigured" in excinfo.value.stderr
    assert "CELERY_TASK_TIME_LIMIT" in excinfo.value.stderr


def test_the_broker_connection_gives_up_instead_of_hanging() -> None:
    options = celery_app.conf.broker_transport_options

    assert options["socket_connect_timeout"] == 2  # noqa: PLR2004
    assert options["socket_timeout"] == 5  # noqa: PLR2004
    assert options["socket_keepalive"] is True
    assert options["health_check_interval"] == 30  # noqa: PLR2004


def test_the_settings_celery_6_will_default_to_are_explicit() -> None:
    conf = celery_app.conf

    # Unset, each of them logs a warning when the worker starts.
    assert conf.broker_connection_retry_on_startup is True
    assert conf.worker_cancel_long_running_tasks_on_connection_loss is True


def test_worker_sizing_and_recycling() -> None:
    conf = celery_app.conf

    assert conf.worker_concurrency == 2  # noqa: PLR2004
    assert conf.worker_max_tasks_per_child == 100  # noqa: PLR2004
    assert conf.worker_max_memory_per_child == 200_000  # noqa: PLR2004
    assert conf.worker_soft_shutdown_timeout == 20.0  # noqa: PLR2004
    assert conf.beat_schedule == {}


def test_django_tasks_run_nothing() -> None:
    # Celery is the task API. An accidental django.tasks enqueue() must not run
    # inside the request, which Django's default (immediate) backend would do.
    assert settings.TASKS == {
        "default": {"BACKEND": "django.tasks.backends.dummy.DummyBackend"},
    }


@pytest.mark.parametrize("module", SETTINGS_MODULES)
def test_eager_mode_exists_only_in_the_test_settings(module: str) -> None:
    result = run_python(
        "import importlib, sys\n"
        "module = importlib.import_module(sys.argv[1])\n"
        "print(getattr(module, 'CELERY_TASK_ALWAYS_EAGER', False))\n",
        module,
    )

    assert result.stdout.strip() == "False"
