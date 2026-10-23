"""Backend contract and errors shared by the translation dispatcher (§6.5)."""
from dataclasses import dataclass


@dataclass(frozen=True)
class BackendCaps:
    io_format: str = "lines"
    max_input_tokens: int = 600
    max_items: int = 16
    max_parallel: int = 1
    output_script: str = "zh-Hans"
    summary: bool = False
    style: bool = False
    metered: bool = False
    placeholder: str = "<{n}>"


class BackendError(RuntimeError):
    pass


class Transient(BackendError):
    pass


class TooLong(BackendError):
    pass


class AuthRequired(BackendError):
    pass


class UsageLimit(BackendError):
    pass


class ConfigError(BackendError):
    pass


class Refused(BackendError):
    pass
