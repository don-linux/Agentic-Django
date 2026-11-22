# Type stubs for django-environ 0.14.0.
# The published package has no py.typed marker and no stubs. Pyright 1.1.414
# infers `default=NOTSET` on unannotated parameters as `NoValue` only, so every
# `env("NAME", default="...")` call in settings is a false error. These stubs
# describe the real signatures (defaults may be a value, None, or unset).

from collections.abc import Iterable
from pathlib import Path
from typing import IO
from typing import Any
from typing import TypeVar
from typing import overload

_T = TypeVar("_T")

class NoValue: ...

class Env:
    NOTSET: NoValue
    scheme: dict[str, Any]

    def __init__(self, **scheme: Any) -> None: ...
    @overload
    def __call__(
        self,
        var: str,
        cast: None = None,
        default: str = ...,
        parse_default: bool = False,
    ) -> str: ...
    @overload
    def __call__(
        self,
        var: str,
        cast: None = None,
        *,
        default: None,
        parse_default: bool = False,
    ) -> str | None: ...
    @overload
    def __call__(
        self,
        var: str,
        cast: None = None,
        default: NoValue = ...,
        parse_default: bool = False,
    ) -> str: ...
    @overload
    def __call__(
        self,
        var: str,
        cast: type[_T],
        default: _T = ...,
        parse_default: bool = False,
    ) -> _T: ...
    @overload
    def __call__(
        self,
        var: str,
        cast: type[_T],
        default: None,
        parse_default: bool = False,
    ) -> _T | None: ...
    @overload
    def __call__(
        self,
        var: str,
        cast: type[_T],
        default: NoValue = ...,
        parse_default: bool = False,
    ) -> _T: ...
    @overload
    def str(
        self,
        var: str,
        default: str = ...,
        multiline: bool = False,
        choices: Iterable[str] | NoValue = ...,
    ) -> str: ...
    @overload
    def str(
        self,
        var: str,
        default: None,
        multiline: bool = False,
        choices: Iterable[str] | NoValue = ...,
    ) -> str | None: ...
    @overload
    def str(
        self,
        var: str,
        default: NoValue = ...,
        multiline: bool = False,
        choices: Iterable[str] | NoValue = ...,
    ) -> str: ...
    @overload
    def bool(self, var: str, default: bool = ...) -> bool: ...
    @overload
    def bool(self, var: str, default: NoValue = ...) -> bool: ...
    @overload
    def int(self, var: str, default: int = ...) -> int: ...
    @overload
    def int(self, var: str, default: None) -> int | None: ...
    @overload
    def int(self, var: str, default: NoValue = ...) -> int: ...
    @overload
    def float(self, var: str, default: float = ...) -> float: ...
    @overload
    def float(self, var: str, default: None) -> float | None: ...
    @overload
    def float(self, var: str, default: NoValue = ...) -> float: ...
    def list(
        self,
        var: str,
        cast: type[_T] | None = None,
        default: list[Any] | NoValue = ...,
    ) -> list[Any]: ...
    def db(
        self,
        var: str = ...,
        default: str | NoValue = ...,
        engine: str | None = None,
    ) -> dict[str, Any]: ...
    def db_url(
        self,
        var: str = ...,
        default: str | NoValue = ...,
        engine: str | None = None,
    ) -> dict[str, Any]: ...
    @classmethod
    def read_env(
        cls,
        env_file: str | Path | IO[str] | None = None,
        overwrite: bool = False,
        parse_comments: bool = False,
        encoding: str = "utf8",
        **overrides: Any,
    ) -> None: ...
