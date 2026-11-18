from __future__ import annotations

import json
import os
import re
import shlex
import subprocess
import tomllib
from pathlib import Path
from typing import Any

import pytest
import yaml

BASE_DIR = Path(__file__).resolve().parents[2]
LOOPBACK = "127.0.0.1:"
# What production publishes: django, behind the host's TLS proxy.
PUBLISHED_IN_PRODUCTION = {"django"}
INTERNAL_IN_PRODUCTION = ["postgres_db", "valkey", "celery_worker", "celery_beat"]
DJANGO_IMAGE_SERVICES = ["django", "celery_worker", "celery_beat"]


def _load(path: str) -> dict[str, Any]:
    return yaml.safe_load((BASE_DIR / path).read_text())


@pytest.fixture(scope="module")
def production() -> dict[str, Any]:
    return _load("docker/prod/compose.yaml")["services"]


@pytest.fixture(scope="module")
def development() -> dict[str, Any]:
    return _load("docker/dev/compose.yaml")["services"]


def _command(service: dict[str, Any]) -> str:
    command = service.get("command", "")
    return " ".join(command) if isinstance(command, list) else str(command)


@pytest.mark.parametrize("name", INTERNAL_IN_PRODUCTION)
def test_internal_services_publish_no_port_in_production(
    production: dict[str, Any],
    name: str,
) -> None:
    # A published port goes around the host firewall. The database, Valkey and
    # the Celery processes are reached over the compose network only.
    assert "ports" not in production[name]


def test_only_django_publishes_a_port_and_on_loopback_by_default(
    production: dict[str, Any],
) -> None:
    published = {name for name, service in production.items() if "ports" in service}

    assert published == PUBLISHED_IN_PRODUCTION
    assert production["django"]["ports"] == [
        "${DOCKER_PROD_DJANGO_BIND:-127.0.0.1}:${DOCKER_PROD_DJANGO_PORT:-8000}:8000",
    ]


def test_the_valkey_password_never_goes_in_the_arguments(
    production: dict[str, Any],
) -> None:
    valkey = production["valkey"]

    # `ps` on the host shows the arguments of every container process.
    assert "--requirepass" not in _command(valkey)
    assert "VALKEY_EXTRA_FLAGS" not in valkey.get("environment", {})
    # It is written to a config file instead, by the image's unprivileged user.
    assert "/run/valkey/valkey.conf" in _command(valkey)
    assert valkey["user"] == "999:1000"


@pytest.mark.parametrize(
    ("password", "maxmemory"),
    [
        ('foo"\nrequirepass ""\nbind 0.0.0.0\n# ', "256mb"),
        ('quote"' + "x" * 40, "256mb"),
        ("back\\slash" + "x" * 40, "256mb"),
        ("carriage\rreturn" + "x" * 40, "256mb"),
        ("x" * 43, "256mb\nbind 0.0.0.0"),
        ("x" * 43, ""),
    ],
)
def test_valkey_refuses_values_that_would_rewrite_its_config(
    production: dict[str, Any],
    tmp_path: Path,
    password: str,
    maxmemory: str,
) -> None:
    # The command writes both values into valkey.conf; a quote or a line break
    # would empty requirepass or add directives such as bind 0.0.0.0.
    conf = tmp_path / "valkey.conf"
    result = _run_valkey_command(production, conf, password, maxmemory)

    assert result.returncode == 1
    assert not conf.exists()
    assert password not in result.stderr


def test_valkey_writes_a_safe_password_into_its_config(
    production: dict[str, Any],
    tmp_path: Path,
) -> None:
    conf = tmp_path / "valkey.conf"
    password = "A-b_c" + "x" * 40
    result = _run_valkey_command(production, conf, password, "256mb")

    # exec valkey-server fails here (no server in the test image): only the
    # config written before it matters.
    assert conf.read_text().splitlines() == [
        f'requirepass "{password}"',
        "dir /data",
        "appendonly yes",
        "appendfsync everysec",
        "maxmemory 256mb",
        "maxmemory-policy volatile-lru",
    ]
    assert result.stderr.count("must") == 0


def _run_valkey_command(
    production: dict[str, Any],
    conf: Path,
    password: str,
    maxmemory: str,
) -> subprocess.CompletedProcess[str]:
    script = production["valkey"]["command"][2].replace("$$", "$")
    script = script.replace("/run/valkey/valkey.conf", str(conf))
    return subprocess.run(  # noqa: S603
        ["/bin/sh", "-c", script],
        env={
            "PATH": os.environ["PATH"],
            "VALKEY_PASSWORD": password,
            "VALKEY_MAXMEMORY": maxmemory,
        },
        capture_output=True,
        text=True,
        check=False,
    )


def test_valkey_keeps_the_task_queue_and_evicts_only_cache_entries(
    production: dict[str, Any],
) -> None:
    command = _command(production["valkey"])

    assert "maxmemory-policy volatile-lru" in command
    assert "appendonly yes" in command


def test_valkey_runs_without_capabilities_on_a_read_only_filesystem(
    production: dict[str, Any],
) -> None:
    valkey = production["valkey"]

    assert valkey["security_opt"] == ["no-new-privileges:true"]
    assert valkey["cap_drop"] == ["ALL"]
    assert valkey["read_only"] is True
    # Docker reads 0 and -1 as "no limit".
    assert valkey["pids_limit"] > 0


def test_valkey_can_still_write_its_data_and_its_config(
    production: dict[str, Any],
) -> None:
    # A read-only root leaves two writable paths, and the server needs both:
    # without the volume the task queue is lost on restart, and without a tmpfs
    # its own user can write to, it cannot create its config and never starts.
    valkey = production["valkey"]
    uid, gid = valkey["user"].split(":")

    assert valkey["volumes"] == ["valkey_data:/data"]
    assert "dir /data" in _command(valkey)
    [tmpfs] = valkey["tmpfs"]
    path, _, options = tmpfs.partition(":")
    assert path == "/run/valkey"
    assert f"uid={uid}" in options.split(",")
    assert f"gid={gid}" in options.split(",")


def test_the_worker_runs_by_default(production: dict[str, Any]) -> None:
    # Behind a profile, a task enqueued by a project that forgot to enable it
    # would wait in the queue forever, without any error.
    assert "profiles" not in production["celery_worker"]


def test_beat_is_optional(production: dict[str, Any]) -> None:
    assert production["celery_beat"]["profiles"] == ["beat"]


def test_django_and_the_celery_services_share_one_image(
    production: dict[str, Any],
) -> None:
    images = {
        production[name]["image"] for name in ("django", "celery_worker", "celery_beat")
    }

    # No release in the name: `build` gets APP_RELEASE, `run` and `up` do not.
    assert images == {"${PROJECT_NAME:-project-name}-prod-django"}


def test_a_missing_project_name_falls_back_to_the_template_name() -> None:
    # Without the default, a missing PROJECT_NAME renders "-prod" and Compose
    # still exits 0.
    compose = _load("docker/prod/compose.yaml")

    assert compose["name"] == "${PROJECT_NAME:-project-name}-prod"
    args = compose["x-django-image"]["build"]["args"]
    assert args["APP_NAME"] == "${PROJECT_NAME:-project-name}"


@pytest.mark.parametrize("name", DJANGO_IMAGE_SERVICES)
def test_production_pins_the_production_settings_module(
    production: dict[str, Any],
    name: str,
) -> None:
    # environment wins over env_file, so a DJANGO_SETTINGS_MODULE line in .env
    # cannot start production on development settings.
    assert production[name]["env_file"] == [".env"]
    environment = production[name]["environment"]
    assert environment["DJANGO_SETTINGS_MODULE"] == "config.settings.production"


@pytest.mark.parametrize("script", ["start.sh", "start-worker.sh"])
@pytest.mark.parametrize(
    "module",
    [None, "", "config.settings.local", "config.settings.test", "config.settings"],
)
def test_start_scripts_refuse_any_other_settings_module(
    script: str,
    module: str | None,
) -> None:
    env = {"PATH": os.environ["PATH"]}
    if module is not None:
        env["DJANGO_SETTINGS_MODULE"] = module
    result = subprocess.run(  # noqa: S603
        ["/bin/sh", str(BASE_DIR / "docker/prod" / script)],
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )

    # It exits before `check --deploy`, so nothing else runs.
    assert result.returncode == 1
    assert result.stdout == ""
    assert result.stderr == (
        "DJANGO_SETTINGS_MODULE must be config.settings.production\n"
    )


@pytest.mark.parametrize(
    "name",
    ["POSTGRES_DB", "POSTGRES_USER", "POSTGRES_PASSWORD"],
)
def test_production_postgres_requires_its_variables(
    production: dict[str, Any],
    name: str,
) -> None:
    # ${VAR:?...} stops `docker compose config` and `up` when the value is unset
    # or empty, instead of starting a database with an empty password.
    value = production["postgres_db"]["environment"][name]

    assert value == f"${{{name}:?Set {name} in docker/prod/.env}}"


def test_the_worker_waits_for_the_migrations(production: dict[str, Any]) -> None:
    depends_on = production["celery_worker"]["depends_on"]

    assert depends_on["django"] == {"condition": "service_healthy"}
    assert depends_on["valkey"] == {"condition": "service_healthy"}


def test_development_ports_listen_on_loopback_only(
    development: dict[str, Any],
) -> None:
    ports = [
        port for service in development.values() for port in service.get("ports", [])
    ]

    assert ports
    assert [port for port in ports if not port.startswith(LOOPBACK)] == []


def test_development_runs_the_worker_and_valkey(development: dict[str, Any]) -> None:
    assert {"django", "celery_worker", "valkey", "postgres_db", "mailpit"} <= set(
        development
    )
    assert "profiles" not in development["celery_worker"]


def test_development_project_and_image_names_default() -> None:
    compose = _load("docker/dev/compose.yaml")
    image = compose["x-django-image"]["image"]

    assert compose["name"] == "${PROJECT_NAME:-project-name}-dev"
    assert image == "${PROJECT_NAME:-project-name}-dev-django"


def test_development_overrides_point_at_compose_services(
    development: dict[str, Any],
) -> None:
    # The root .env is shared with runs outside Docker (localhost); inside the
    # stack these overrides send each service to its Compose neighbour.
    for name in ("django", "celery_worker"):
        environment = development[name]["environment"]
        assert environment["POSTGRES_HOST"] == "postgres_db"
        assert environment["DJANGO_MAILER_HOST"] == "mailpit"
        assert environment["VALKEY_URL"] == "redis://valkey:6379"
    assert "DJANGO_SETTINGS_MODULE" not in development["django"]["environment"]
    assert (
        development["celery_worker"]["environment"]["DJANGO_SETTINGS_MODULE"]
        == "config.settings.local"
    )


def test_development_runs_as_the_host_user(development: dict[str, Any]) -> None:
    # As root, every file the container writes to the bind-mounted checkout
    # would belong to root on a Linux host.
    dockerfile = (BASE_DIR / "docker/dev/Dockerfile").read_text().splitlines()
    users = [line for line in dockerfile if line.startswith("USER ")]
    example = (BASE_DIR / "docker/dev/.env.example").read_text().splitlines()

    assert users == ["USER django"]
    assert "ARG HOST_UID=1000" in dockerfile
    assert "ARG HOST_GID=1000" in dockerfile
    for name in ("django", "celery_worker"):
        assert development[name]["build"]["args"] == {
            "HOST_UID": "${HOST_UID:-1000}",
            "HOST_GID": "${HOST_GID:-1000}",
        }
        assert "user" not in development[name]
    assert {"HOST_UID=1000", "HOST_GID=1000"} <= set(example)


def test_development_waits_for_its_dependencies_and_forwards_signals(
    development: dict[str, Any],
) -> None:
    assert "healthcheck" in development["postgres_db"]
    assert "healthcheck" in development["valkey"]
    django = _command(development["django"])
    worker = _command(development["celery_worker"])
    for name in ("django", "celery_worker"):
        assert development[name]["init"] is True
        assert development[name]["volumes"] == ["../../:/app:z"]
    assert "migrate --noinput && exec python manage.py runserver" in django
    # The worker waits for the migrations instead of racing django to apply them.
    assert "until python manage.py migrate --check" in worker
    assert "exec celery --app config worker" in worker
    assert "migrate --noinput" not in worker
    assert development["postgres_db"]["shm_size"] == "256mb"
    assert "@sha256:" in development["mailpit"]["image"]


def test_production_builds_the_django_user_from_the_uid_args() -> None:
    args = _load("docker/prod/compose.yaml")["x-django-image"]["build"]["args"]
    dockerfile = (BASE_DIR / "docker/prod/Dockerfile").read_text().splitlines()

    assert args["USER_UID"] == "${DOCKER_PROD_USER_UID:-1000}"
    assert args["USER_GID"] == "${DOCKER_PROD_USER_GID:-1000}"
    assert {"ARG USER_UID=1000", "ARG USER_GID=1000"} <= set(dockerfile)


def test_the_uv_image_is_pinned_by_digest() -> None:
    # Dependabot updates neither COPY --from line.
    for path in ("docker/prod/Dockerfile", "docker/dev/Dockerfile"):
        text = (BASE_DIR / path).read_text()
        assert re.search(r"ghcr\.io/astral-sh/uv:[0-9.]+@sha256:[0-9a-f]{64} ", text)


def _load_jsonc(path: Path) -> dict[str, Any]:
    # devcontainer.json allows // comments; drop them outside strings.
    raw = path.read_text()
    out: list[str] = []
    in_string = False
    escape = False
    i = 0
    while i < len(raw):
        char = raw[i]
        if in_string:
            out.append(char)
            if escape:
                escape = False
            elif char == "\\":
                escape = True
            elif char == '"':
                in_string = False
            i += 1
            continue
        if char == '"':
            in_string = True
        elif raw.startswith("//", i):
            while i < len(raw) and raw[i] not in "\n\r":
                i += 1
            continue
        out.append(char)
        i += 1
    loaded = json.loads("".join(out))
    assert isinstance(loaded, dict)
    return loaded


def test_devcontainer_targets_the_django_service() -> None:
    data = _load_jsonc(BASE_DIR / ".devcontainer/devcontainer.json")

    assert data["service"] == "django"
    assert data["overrideCommand"] is False
    assert data["workspaceFolder"] == "/app"
    assert data["dockerComposeFile"] == ["../docker/dev/compose.yaml", "compose.yaml"]
    assert data["remoteUser"] == "django"
    assert data["updateRemoteUserUID"] is True
    for compose_file in data["dockerComposeFile"]:
        assert (BASE_DIR / ".devcontainer" / compose_file).is_file()


def test_devcontainer_copies_missing_env_files_and_keeps_existing_ones(
    tmp_path: Path,
) -> None:
    # initializeCommand runs on the host, in the checkout, before Compose reads
    # both env files.
    data = _load_jsonc(BASE_DIR / ".devcontainer/devcontainer.json")
    command = shlex.split(data["initializeCommand"])
    (tmp_path / "docker/dev").mkdir(parents=True)
    (tmp_path / ".env.example").write_text("ROOT=example\n")
    (tmp_path / "docker/dev/.env.example").write_text("DEV=example\n")

    subprocess.run(command, cwd=tmp_path, check=True)  # noqa: S603

    assert (tmp_path / ".env").read_text() == "ROOT=example\n"
    assert (tmp_path / "docker/dev/.env").read_text() == "DEV=example\n"

    (tmp_path / ".env").write_text("ROOT=mine\n")
    (tmp_path / "docker/dev/.env").write_text("DEV=mine\n")
    subprocess.run(command, cwd=tmp_path, check=True)  # noqa: S603

    assert (tmp_path / ".env").read_text() == "ROOT=mine\n"
    assert (tmp_path / "docker/dev/.env").read_text() == "DEV=mine\n"


def test_the_valkey_image_is_the_same_everywhere(
    production: dict[str, Any],
    development: dict[str, Any],
) -> None:
    # Dependabot updates the compose files but not service images in workflows.
    images = {production["valkey"]["image"], development["valkey"]["image"]}
    workflow = _load(".github/workflows/ci.yml")
    for job in workflow["jobs"].values():
        valkey = job.get("services", {}).get("valkey")
        if valkey is not None:
            images.add(valkey["image"])

    assert len(images) == 1
    assert "@sha256:" in images.pop()


def test_dependabot_leaves_major_valkey_upgrades_to_a_person(
    production: dict[str, Any],
) -> None:
    config = _load(".github/dependabot.yml")
    [compose] = [
        update
        for update in config["updates"]
        if update["package-ecosystem"] == "docker-compose"
    ]
    rules = {
        rule["dependency-name"]: rule.get("update-types") for rule in compose["ignore"]
    }
    # Dependabot names an image by its repository. A rule under another name
    # (or in the "docker" entry, which only reads Dockerfiles) matches nothing.
    repository = production["valkey"]["image"].split(":")[0]

    assert {"/docker/dev", "/docker/prod"} <= set(compose["directories"])
    # Majors only. A rule without update-types would ignore every release, and
    # the minor and patch ones carry the security fixes.
    assert rules.get(repository) == ["version-update:semver-major"]


def test_the_postgres_image_is_the_same_everywhere(
    production: dict[str, Any],
    development: dict[str, Any],
) -> None:
    # Dependabot updates the compose files but not service images in workflows.
    images = {production["postgres_db"]["image"], development["postgres_db"]["image"]}
    workflow = _load(".github/workflows/ci.yml")
    for job in workflow["jobs"].values():
        postgres = job.get("services", {}).get("postgres")
        if postgres is not None:
            images.add(postgres["image"])

    assert len(images) == 1
    assert "@sha256:" in images.pop()


_UV_IMAGE = re.compile(r"ghcr\.io/astral-sh/uv:(\d+\.\d+\.\d+)\b")


def _uv_version_from_setup_action() -> str:
    action = _load(".github/actions/setup-python-env/action.yml")
    for step in action["runs"]["steps"]:
        if "setup-uv" in str(step.get("uses", "")):
            version = step.get("with", {}).get("version")
            assert version, "setup-uv has no version pin"
            assert str(version) != "latest"
            return str(version)
    msg = "setup-uv step not found"
    raise AssertionError(msg)


def _uv_version_from_dockerfile(path: str) -> str:
    found = _UV_IMAGE.findall((BASE_DIR / path).read_text())
    assert found, f"no ghcr.io/astral-sh/uv pin in {path}"
    assert len(set(found)) == 1, f"uv pins inside {path} disagree: {found}"
    return str(found[0])


def _uv_version_from_pre_commit() -> str:
    config = _load(".pre-commit-config.yaml")
    for repo in config["repos"]:
        if str(repo.get("repo", "")).rstrip("/").endswith("astral-sh/uv-pre-commit"):
            return str(repo["rev"])
    msg = "uv-pre-commit hook not found"
    raise AssertionError(msg)


def test_the_uv_version_is_the_same_everywhere() -> None:
    # Dependabot updates none of these four pins together (see the comment on
    # the setup-uv step and the Dependabot ignore for uv-pre-commit).
    versions = {
        _uv_version_from_setup_action(),
        _uv_version_from_dockerfile("docker/prod/Dockerfile"),
        _uv_version_from_dockerfile("docker/dev/Dockerfile"),
        _uv_version_from_pre_commit(),
    }

    assert len(versions) == 1
    assert re.fullmatch(r"\d+\.\d+\.\d+", versions.pop())


@pytest.mark.parametrize(
    ("hook_repo", "package"),
    [
        ("astral-sh/ruff-pre-commit", "ruff"),
        ("adamchainz/django-upgrade", "django-upgrade"),
    ],
)
def test_pre_commit_and_pyproject_pin_the_same_version(
    hook_repo: str, package: str
) -> None:
    # Dependabot bumps the hook and the dev dependency in separate pull requests.
    revs = [
        str(repo["rev"]).removeprefix("v")
        for repo in _load(".pre-commit-config.yaml")["repos"]
        if str(repo.get("repo", "")).rstrip("/").endswith(hook_repo)
    ]
    pyproject = tomllib.loads((BASE_DIR / "pyproject.toml").read_text())
    pins = [
        requirement.split("==", 1)[1]
        for requirement in pyproject["dependency-groups"]["dev"]
        if requirement.split("==", 1)[0] == package
    ]

    assert len(revs) == 1, f"{hook_repo} hook not found"
    assert pins == revs
