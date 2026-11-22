# Type stubs for django-environ 0.14.0.
# The published package has no py.typed marker and no stubs. Pyright 1.1.414
# infers `default=NOTSET` on unannotated parameters as `NoValue` only, so every
# `env("NAME", default="...")` call in settings is a false error. These stubs
# describe the real signatures (defaults may be a value, None, or unset).
# Env defines methods named str/bool/int/float/list, which shadow the builtins
# inside the class body, so annotations there spell builtins.str and so on.

import builtins
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
    scheme: dict[builtins.str, Any]

    def __init__(self, **scheme: Any) -> None: ...
    @overload
    def __call__(
        self,
        var: builtins.str,
        cast: None = None,
        default: builtins.str = ...,
        parse_default: builtins.bool = False,
    ) -> builtins.str: ...
    @overload
    def __call__(
        self,
        var: builtins.str,
        cast: None = None,
        *,
        default: None,
        parse_default: builtins.bool = False,
    ) -> builtins.str | None: ...
    @overload
    def __call__(
        self,
        var: builtins.str,
        cast: None = None,
        default: NoValue = ...,
        parse_default: builtins.bool = False,
    ) -> builtins.str: ...
    @overload
    def __call__(
        self,
        var: builtins.str,
        cast: type[_T],
        default: _T = ...,
        parse_default: builtins.bool = False,
    ) -> _T: ...
    @overload
    def str(
        self,
        var: builtins.str,
        default: builtins.str = ...,
        multiline: builtins.bool = False,
        choices: Iterable[builtins.str] | NoValue = ...,
    ) -> builtins.str: ...
    @overload
    def str(
        self,
        var: builtins.str,
        default: None,
        multiline: builtins.bool = False,
        choices: Iterable[builtins.str] | NoValue = ...,
    ) -> builtins.str | None: ...
    @overload
    def str(
        self,
        var: builtins.str,
        default: NoValue = ...,
        multiline: builtins.bool = False,
        choices: Iterable[builtins.str] | NoValue = ...,
    ) -> builtins.str: ...
    @overload
    def bool(
        self, var: builtins.str, default: builtins.bool = ...
    ) -> builtins.bool: ...
    @overload
    def bool(self, var: builtins.str, default: NoValue = ...) -> builtins.bool: ...
    @overload
    def int(self, var: builtins.str, default: builtins.int = ...) -> builtins.int: ...
    @overload
    def int(self, var: builtins.str, default: None) -> builtins.int | None: ...
    @overload
    def int(self, var: builtins.str, default: NoValue = ...) -> builtins.int: ...
    @overload
    def float(
        self, var: builtins.str, default: builtins.float = ...
    ) -> builtins.float: ...
    @overload
    def float(self, var: builtins.str, default: None) -> builtins.float | None: ...
    @overload
    def float(self, var: builtins.str, default: NoValue = ...) -> builtins.float: ...
    def list(
        self,
        var: builtins.str,
        cast: type[_T] | None = None,
        default: builtins.list[Any] | NoValue = ...,
    ) -> builtins.list[Any]: ...
    def db(
        self,
        var: builtins.str = ...,
        default: builtins.str | NoValue = ...,
        engine: builtins.str | None = None,
    ) -> dict[builtins.str, Any]: ...
    def db_url(
        self,
        var: builtins.str = ...,
        default: builtins.str | NoValue = ...,
        engine: builtins.str | None = None,
    ) -> dict[builtins.str, Any]: ...
    @classmethod
    def read_env(
        cls,
        env_file: builtins.str | Path | IO[builtins.str] | None = None,
        overwrite: builtins.bool = False,
        parse_comments: builtins.bool = False,
        encoding: builtins.str = "utf8",
        **overrides: Any,
    ) -> None: ...
