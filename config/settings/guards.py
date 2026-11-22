"""
Startup guards: production refuses example or weak values, local settings refuse
remote servers. Imports without any production variable, so tests call the guards
directly; messages name the variable and never print its value.
See docs/security.md#production-secrets.
"""

from __future__ import annotations

import ipaddress
import re
from typing import TYPE_CHECKING
from typing import Any
from urllib.parse import parse_qs
from urllib.parse import urlsplit

from django.core.exceptions import ImproperlyConfigured

if TYPE_CHECKING:
    from collections.abc import Iterable

PLACEHOLDER = "change-me"
PLACEHOLDER_COMPACT = "changeme"
_SEPARATORS = re.compile(r"[-_\s]+")
SMTP_BACKEND = "django.core.mail.backends.smtp.EmailBackend"
# RFC 2606 / 6761 documentation names. Subdomains (app.example.com) count too.
# .invalid and .test are left allowed: CI and the test helpers use them, and they
# never resolve on the public internet.
EXAMPLE_DOMAINS = ("example.com", "example.org", "example.net")
LOCAL_HOSTS = frozenset({"localhost", "127.0.0.1", "::1"})
# A local SMTP relay is accepted on purpose (documented in the prod example).
LOCAL_RELAY_HOSTS = frozenset({"localhost", "127.0.0.1", "::1"})
INSECURE_KEY_PREFIX = "django-insecure-"
# Same thresholds as Django's security.W009 check.
MIN_SECRET_KEY_LENGTH = 50
MIN_SECRET_KEY_UNIQUE_CHARACTERS = 5
VALKEY_SCHEMES = ("redis", "rediss")
# secrets.token_urlsafe(32) gives 43 characters.
MIN_VALKEY_PASSWORD_LENGTH = 32
VALKEY_PASSWORD_FORBIDDEN = frozenset('"\\\n\r')
# Values of ssl_check_hostname that turn the host name check off, in lowercase.
# redis-py, behind the cache, reads all of them as false; kombu only "false".
HOST_NAME_CHECK_OFF = frozenset({"0", "f", "false", "n", "no"})
# First path segments the app serves itself. config/urls.py mounts the admin first,
# so an admin URL under one of them would hide those routes.
RESERVED_ADMIN_SEGMENTS = frozenset({"api", "_allauth", "static", "media", "health"})
# Preload-list rule (https://hstspreload.org): one year and includeSubDomains.
HSTS_PRELOAD_MIN_SECONDS = 31_536_000


def contains_placeholder(value: str) -> bool:
    """
    Return True if value is a change-me placeholder, ignoring case and separators.

    Matches change-me, CHANGE-ME, changeme, change_me and "change me". The
    message never includes the value.
    """
    folded = value.casefold()
    return PLACEHOLDER in folded or PLACEHOLDER_COMPACT in _SEPARATORS.sub("", folded)


def normalize_host(value: str) -> str:
    """Return a hostname without a leading dot, trailing dot, brackets, or port."""
    host = value.strip().rstrip(".").casefold()
    if host.startswith("[") and "]" in host:
        # [::1] or [::1]:port — only the address is compared.
        inside, _, _rest = host[1:].partition("]")
        return inside
    if host.startswith("."):
        host = host[1:]
    if host.count(":") == 1:
        host, _, _port = host.partition(":")
    return host


def is_placeholder_host(value: str) -> bool:
    """
    Return True if value is localhost, an RFC example domain, or a subdomain.

    `notexample.com` and `example.com.mx` are not example domains. `.invalid`
    and `.test` are not rejected: they are reserved for tests and CI.
    """
    host = normalize_host(value)
    if not host:
        return False
    if host in LOCAL_HOSTS or host.endswith(".localhost"):
        return True
    return any(
        host == domain or host.endswith(f".{domain}") for domain in EXAMPLE_DOMAINS
    )


def hosts_in_value(value: str) -> list[str]:
    """Extract hostnames from a host, origin URL, or email address."""
    text = value.strip()
    if not text:
        return []
    if "://" in text:
        host = urlsplit(text).hostname
        return [host] if host else []
    if "@" in text:
        domain = text.rsplit("@", 1)[-1].strip().rstrip(">")
        return [domain] if domain else []
    return [text]


def is_placeholder_location(value: str) -> bool:
    """Return True if a host, URL, or email uses an example or localhost host."""
    return any(is_placeholder_host(host) for host in hosts_in_value(value))


def assert_production_secrets(
    *,
    secret_key: str,
    secret_key_fallbacks: Iterable[str],
    admin_url: str,
    db_password: str | None,
) -> None:
    """Raise ImproperlyConfigured if any secret is a placeholder or too weak."""
    problems = _secret_key_problems("DJANGO_SECRET_KEY", secret_key)
    for index, fallback in enumerate(secret_key_fallbacks):
        problems += _secret_key_problems(
            f"DJANGO_SECRET_KEY_FALLBACKS (entry {index + 1})",
            fallback,
        )

    if contains_placeholder(admin_url):
        problems.append("DJANGO_ADMIN_URL still contains the example value.")
    if admin_url == "admin/":
        problems.append("DJANGO_ADMIN_URL must not be Django's default admin path.")
    if not admin_url.endswith("/"):
        problems.append("DJANGO_ADMIN_URL must end with '/'.")
    if admin_url.split("/", 1)[0].casefold() in RESERVED_ADMIN_SEGMENTS:
        problems.append(
            "DJANGO_ADMIN_URL must not start with a path the app serves "
            "(api/, _allauth/, static/, media/ or health/).",
        )

    # None means no password at all (peer auth or a password-less DATABASE_URL);
    # an empty string is a value left blank by mistake.
    if db_password is not None and not db_password:
        problems.append("POSTGRES_PASSWORD must not be empty.")
    elif db_password and contains_placeholder(db_password):
        problems.append("POSTGRES_PASSWORD still contains the example value.")

    _raise_if_any(problems)


def assert_production_hosts(
    *,
    allowed_hosts: Iterable[str],
    csrf_trusted_origins: Iterable[str] = (),
    cors_allowed_origins: Iterable[str] = (),
) -> None:
    """
    Raise ImproperlyConfigured if production hosts are empty, wildcards, or examples.

    CSRF and CORS origins may be empty (token-only or same-origin clients). When
    they are set, they must be the real frontend origin, not an RFC example or
    localhost.
    """
    problems = _allowed_host_problems(allowed_hosts)
    problems += _origin_problems("DJANGO_CSRF_TRUSTED_ORIGINS", csrf_trusted_origins)
    problems += _origin_problems("CORS_ALLOWED_ORIGINS", cors_allowed_origins)
    _raise_if_any(problems)


def _origin_problems(variable: str, origins: Iterable[str]) -> list[str]:
    # Django compares the Origin header as an exact string, so a path or a
    # trailing slash never matches what a browser sends; django-cors-headers
    # compares scheme and host, and its corsheaders.E014 check refuses a path.
    problems = []
    for origin in origins:
        parts = urlsplit(origin)
        if parts.path or parts.query or parts.fragment:
            problems.append(
                f"{variable} must be scheme://host, with no trailing slash or path.",
            )
        if parts.scheme != "https":
            problems.append(f"{variable} must use https.")
        if is_placeholder_location(origin):
            problems.append(
                f"{variable} must be the real frontend origin, not an example "
                "or localhost value.",
            )
    return problems


def _allowed_host_problems(allowed_hosts: Iterable[str]) -> list[str]:
    problems = []
    hosts = list(allowed_hosts)
    if not hosts:
        problems.append("DJANGO_ALLOWED_HOSTS must list real hosts.")
    if "*" in hosts:
        problems.append("DJANGO_ALLOWED_HOSTS must list real hosts, not '*'.")
    elif any("*" in host for host in hosts):
        problems.append(
            "DJANGO_ALLOWED_HOSTS does not treat '*.domain' as a wildcard: "
            "use '.domain' for subdomains.",
        )
    if any(host != "*" and is_placeholder_host(host) for host in hosts):
        problems.append(
            "DJANGO_ALLOWED_HOSTS must be the real API host, not an example "
            "or localhost value.",
        )
    return problems


def assert_production_email(
    *,
    mailers: dict[str, Any],
    default_from_email: str,
    frontend_url: str,
) -> None:
    """
    Raise ImproperlyConfigured if production email would not reach real users.

    Verification is mandatory, so without working email nobody can finish signup.
    A local relay (DJANGO_MAILER_HOST=localhost) is accepted on purpose.
    """
    problems = []
    default = mailers.get("default", {})
    backend = default.get("BACKEND", "")
    options = default.get("OPTIONS", {})
    if backend != SMTP_BACKEND and not backend.startswith("anymail."):
        problems.append(
            "MAILERS['default'] must send real email (SMTP or django-anymail), "
            "not a console, file, memory or dummy backend.",
        )
    if backend == SMTP_BACKEND:
        host = str(options.get("host") or "").strip()
        if not host or (
            normalize_host(host) not in LOCAL_RELAY_HOSTS and is_placeholder_host(host)
        ):
            problems.append("DJANGO_MAILER_HOST must be your provider's SMTP host.")
        if options.get("use_tls") and options.get("use_ssl"):
            problems.append(
                "DJANGO_MAILER_USE_TLS and DJANGO_MAILER_USE_SSL cannot both be True.",
            )
    if not default_from_email.strip():
        problems.append("DJANGO_DEFAULT_FROM_EMAIL is required.")
    elif is_placeholder_location(default_from_email):
        problems.append(
            "DJANGO_DEFAULT_FROM_EMAIL must use a domain verified with your provider.",
        )
    if not frontend_url.strip():
        problems.append("FRONTEND_URL is required.")
    elif is_placeholder_location(frontend_url):
        problems.append("FRONTEND_URL must be the real frontend (used in email links).")
    elif urlsplit(frontend_url.strip()).scheme != "https":
        problems.append("FRONTEND_URL must use https.")
    _raise_if_any(problems)


def assert_production_valkey(
    *,
    url: str,
    password: str,
    cache_db: int,
    broker_db: int,
) -> None:
    """
    Raise ImproperlyConfigured if Valkey would run unprotected or misaddressed.

    Whoever can write to it runs code in Django and signs in as anyone (pickles and
    sessions). See docs/security.md#valkey-access.
    """
    problems = []
    parts = urlsplit(url)
    if parts.scheme not in VALKEY_SCHEMES:
        problems.append(
            "VALKEY_URL must start with redis:// or rediss:// (not valkey://).",
        )
    if "@" in parts.netloc:
        problems.append("VALKEY_URL must not contain credentials: use VALKEY_PASSWORD.")
    if _is_unverified_tls(url):
        problems.append(
            "VALKEY_URL uses rediss:// without ssl_cert_reqs=required, or turns the "
            "host name check off with ssl_check_hostname: the server would not be "
            "verified. End the URL with ?ssl_cert_reqs=required and remove "
            "ssl_check_hostname.",
        )
    if not password:
        problems.append("VALKEY_PASSWORD is required: Valkey never runs without one.")
    elif contains_placeholder(password):
        problems.append("VALKEY_PASSWORD still contains the example value.")
    elif len(password) < MIN_VALKEY_PASSWORD_LENGTH:
        problems.append(
            f"VALKEY_PASSWORD must have at least {MIN_VALKEY_PASSWORD_LENGTH} "
            "characters.",
        )
    if any(char in password for char in VALKEY_PASSWORD_FORBIDDEN):
        # docker/prod/compose.yaml writes it inside double quotes in valkey.conf.
        problems.append(
            "VALKEY_PASSWORD must not contain quotes, backslashes or line breaks.",
        )
    if cache_db == broker_db:
        problems.append(
            "VALKEY_CACHE_DB and VALKEY_BROKER_DB must differ: the cache and the "
            "task broker never share a database.",
        )
    _raise_if_any(problems)


def assert_production_celery(*, always_eager: bool, broker_url: str) -> None:
    """
    Raise ImproperlyConfigured if a task would not reach a real worker.

    No eager mode or memory:// broker, and a rediss:// broker must verify the server.
    The message never shows the broker URL. See docs/security.md#production-secrets.
    """
    problems = []
    if always_eager:
        problems.append("CELERY_TASK_ALWAYS_EAGER must be off: it is for tests only.")
    if urlsplit(broker_url).scheme not in VALKEY_SCHEMES:
        problems.append(
            "The Celery broker must be redis:// or rediss://: check VALKEY_URL, "
            "or CELERY_BROKER_URL if it is set.",
        )
    if _is_unverified_tls(broker_url):
        problems.append(
            "The Celery broker uses rediss:// without ssl_cert_reqs=required, or "
            "turns the host name check off with ssl_check_hostname, so it would not "
            "verify the server: use ?ssl_cert_reqs=required and no "
            "ssl_check_hostname in VALKEY_URL, or in CELERY_BROKER_URL if it is set.",
        )
    _raise_if_any(problems)


def assert_production_hsts(
    *,
    seconds: int,
    include_subdomains: bool,
    preload: bool,
) -> None:
    """
    Raise ImproperlyConfigured if HSTS is off or a flag comes before one year.

    See docs/security.md#hsts.
    """
    problems = []
    if seconds <= 0:
        problems.append(
            "DJANGO_SECURE_HSTS_SECONDS must be a positive number of seconds.",
        )
    if (include_subdomains or preload) and seconds < HSTS_PRELOAD_MIN_SECONDS:
        problems.append(
            "DJANGO_SECURE_HSTS_INCLUDE_SUBDOMAINS and DJANGO_SECURE_HSTS_PRELOAD "
            "require DJANGO_SECURE_HSTS_SECONDS of at least "
            f"{HSTS_PRELOAD_MIN_SECONDS} (1 year).",
        )
    if preload and not include_subdomains:
        problems.append(
            "DJANGO_SECURE_HSTS_PRELOAD requires "
            "DJANGO_SECURE_HSTS_INCLUDE_SUBDOMAINS to be True.",
        )
    _raise_if_any(problems)


def silenced_hsts_system_checks(
    *,
    include_subdomains: bool,
    preload: bool,
) -> list[str]:
    """
    System checks to silence while the matching HSTS flag is still off.

    See docs/security.md#hsts.
    """
    silenced: list[str] = []
    if not include_subdomains:
        silenced.append("security.W005")
    if not preload:
        silenced.append("security.W021")
    return silenced


# Loopback plus the service names used in docker/dev/compose.yaml and
# docker/prod/compose.yaml. A hosted dev database needs DJANGO_ALLOW_REMOTE_LOCAL.
LOCAL_DATABASE_HOSTS = frozenset({"postgres_db", "postgres", "db"})
LOCAL_VALKEY_HOSTS = frozenset({"valkey"})


# Ninja 1.8.0 SimpleRateThrottle.parse_rate: "<int>/[<int>]<unit>" with unit s, sec,
# m, min, h, hour, d or day, or "<int>/<seconds>". "60/minute" or "60/hours" raise
# ValueError when core/api/api.py builds the throttles: every URL would be a 500.
# Source: ninja/throttling.py; https://django-ninja.dev/guides/throttling
_THROTTLE_RATE = re.compile(r"[0-9]+/(?:[0-9]+|[0-9]*(?:s|sec|m|min|h|hour|d|day))")


def assert_throttle_rates(rates: dict[str, str | None]) -> None:
    """
    Raise ImproperlyConfigured if a throttle rate cannot be parsed.

    A typo would otherwise be a 500 on the first request.
    Every environment runs this. See docs/api.md#throttling.
    """
    problems = [
        f"Throttle rate {scope!r} must be <number>/<s|m|h|d>, like 60/min "
        "(anon and user come from DJANGO_THROTTLE_ANON_RATE and "
        "DJANGO_THROTTLE_USER_RATE)."
        for scope, rate in rates.items()
        if rate is not None and not _THROTTLE_RATE.fullmatch(rate)
    ]
    if problems:
        msg = "Refusing to start with invalid throttle rates:\n- " + "\n- ".join(
            problems
        )
        raise ImproperlyConfigured(msg)


def assert_local_targets(
    *,
    database_host: str,
    valkey_url: str,
    allow_remote: bool,
    celery_broker_url: str = "",
) -> None:
    """
    Raise ImproperlyConfigured if local settings would talk to a remote server.

    DJANGO_ALLOW_REMOTE_LOCAL=true allows a hosted development database. Messages
    never echo the host, the URL or credentials. See docs/settings.md#local.
    """
    if allow_remote:
        return
    problems = []
    if not _is_local_host(database_host, LOCAL_DATABASE_HOSTS):
        problems.append(
            "DATABASE_URL / POSTGRES_HOST is not a loopback address or a compose "
            "service name (localhost, 127.0.0.1, postgres_db). Local settings "
            "refuse a remote database; set DJANGO_ALLOW_REMOTE_LOCAL=true if "
            "this is intentional.",
        )
    valkey_host = urlsplit(valkey_url).hostname or ""
    if not _is_local_host(valkey_host, LOCAL_VALKEY_HOSTS):
        problems.append(
            "VALKEY_URL is not a loopback address or the compose service name "
            "(localhost, 127.0.0.1, valkey). Local settings refuse a remote "
            "Valkey; set DJANGO_ALLOW_REMOTE_LOCAL=true if this is intentional.",
        )
    if celery_broker_url:
        broker_host = urlsplit(celery_broker_url).hostname or ""
        if not _is_local_host(broker_host, LOCAL_VALKEY_HOSTS):
            problems.append(
                "CELERY_BROKER_URL is not a loopback address or the compose "
                "service name (localhost, 127.0.0.1, valkey). Local settings "
                "refuse a remote broker; set DJANGO_ALLOW_REMOTE_LOCAL=true if "
                "this is intentional.",
            )
    if problems:
        msg = (
            "Refusing to start local settings against a remote server:\n- "
            + "\n- ".join(
                problems,
            )
        )
        raise ImproperlyConfigured(msg)


def _is_local_host(host: str, service_names: frozenset[str]) -> bool:
    """Return True for an empty host (Unix socket), loopback, or a compose service."""
    normalized = host.strip().lower().removeprefix("[").removesuffix("]")
    if not normalized or normalized == "localhost" or normalized in service_names:
        return True
    try:
        return ipaddress.ip_address(normalized).is_loopback
    except ValueError:
        return False


def _is_unverified_tls(url: str) -> bool:
    """
    Return True for a rediss:// URL that would not verify the server it reaches.

    ssl_cert_reqs must be the lowercase "required", ssl_check_hostname must not be
    off, and a repeated parameter is judged by all its values.
    See docs/security.md#valkey-access.
    """
    parts = urlsplit(url)
    if parts.scheme != "rediss":
        return False
    query = parse_qs(parts.query)
    return query.get("ssl_cert_reqs") != ["required"] or any(
        value.lower() in HOST_NAME_CHECK_OFF
        for value in query.get("ssl_check_hostname", [])
    )


def _secret_key_problems(name: str, key: str) -> list[str]:
    problems = []
    if contains_placeholder(key):
        problems.append(f"{name} still contains the example value.")
    if key.startswith(INSECURE_KEY_PREFIX):
        problems.append(f"{name} is a development key ({INSECURE_KEY_PREFIX}...).")
    if (
        len(key) < MIN_SECRET_KEY_LENGTH
        or len(set(key)) < MIN_SECRET_KEY_UNIQUE_CHARACTERS
    ):
        problems.append(
            f"{name} must have at least {MIN_SECRET_KEY_LENGTH} characters and "
            f"{MIN_SECRET_KEY_UNIQUE_CHARACTERS} different ones.",
        )
    return problems


def _raise_if_any(problems: list[str]) -> None:
    if problems:
        msg = "Refusing to start with unsafe production settings:\n- " + "\n- ".join(
            problems,
        )
        raise ImproperlyConfigured(msg)
