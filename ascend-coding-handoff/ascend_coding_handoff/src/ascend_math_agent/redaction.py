"""Central, deterministic secret redaction for artifacts and structured logs."""

from __future__ import annotations

import os
import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import asdict, dataclass, is_dataclass
from enum import Enum
from pathlib import Path
from typing import Any, Generic, TypeVar

from pydantic import BaseModel

REDACTED = "[REDACTED]"

T = TypeVar("T")


@dataclass(frozen=True, slots=True)
class RedactionResult(Generic[T]):
    value: T
    replacements: int = 0
    categories: tuple[str, ...] = ()

    @property
    def changed(self) -> bool:
        return self.replacements > 0


_TOKEN_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    (
        "openai_token",
        re.compile(r"(?<![A-Za-z0-9_-])sk-(?:proj-)?[A-Za-z0-9_-]{8,}"),
    ),
    (
        "github_token",
        re.compile(r"(?<![A-Za-z0-9_])gh[opusr]_[A-Za-z0-9_]{12,}"),
    ),
    (
        "slack_token",
        re.compile(r"(?<![A-Za-z0-9-])xox[baprs]-[A-Za-z0-9-]{10,}"),
    ),
    (
        "jwt_token",
        re.compile(
            r"(?<![A-Za-z0-9_-])eyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\."
            r"[A-Za-z0-9_-]{8,}"
        ),
    ),
    (
        "bearer_token",
        re.compile(r"(?i)(\bbearer\s+)[A-Za-z0-9._~+/=-]{6,}"),
    ),
    (
        "authorization_header",
        re.compile(r"(?i)(\bauthorization\s*[:=]\s*)(?!\s*\[REDACTED\])[^\r\n,;]+"),
    ),
    (
        "secret_assignment",
        re.compile(
            r"(?i)(\b(?:openai_api_key|api[_-]?key|access[_-]?token|auth[_-]?token|"
            r"client[_-]?secret|password)\b\s*[:=]\s*)"
            r"(?!\s*\[REDACTED\])(?:[\"']?)[^\s\"',;}{]+(?:[\"']?)"
        ),
    ),
    (
        "secret_url_parameter",
        re.compile(
            r"(?i)([?&](?:api[_-]?key|access[_-]?token|token|secret|password)=)"
            r"(?!\[REDACTED\])[^&#\s]+"
        ),
    ),
)

_SENSITIVE_KEY = re.compile(
    r"(?i)(?:^|[_\-.])(?:authorization|proxy_authorization|api_?key|access_?token|"
    r"auth_?token|secret|password|passwd|credential|cookie)(?:$|[_\-.])"
)

_SENSITIVE_ENVIRONMENT_NAME = re.compile(
    r"(?i)(?:api[_-]?key|access[_-]?(?:key|token)|auth(?:orization)?[_-]?token|"
    r"bearer|cookie|credential|password|passwd|secret|session[_-]?token|"
    r"(?:^|_)token(?:_|$)|askpass|ssh_auth_sock)"
)


class SecretRedactor:
    """Redact known token shapes, sensitive mapping keys, and supplied secret values."""

    def __init__(self, secrets: Iterable[str] = ()) -> None:
        # Longest first prevents a shorter credential fragment from leaving a suffix.
        self._secrets = tuple(
            sorted(
                {secret for secret in secrets if secret and len(secret) >= 4},
                key=len,
                reverse=True,
            )
        )

    def redact_text_result(self, text: str) -> RedactionResult[str]:
        redacted = text
        count = 0
        categories: set[str] = set()

        for secret in self._secrets:
            occurrences = redacted.count(secret)
            if occurrences:
                redacted = redacted.replace(secret, REDACTED)
                count += occurrences
                categories.add("explicit_secret")

        for category, pattern in _TOKEN_PATTERNS:
            if category in {
                "bearer_token",
                "authorization_header",
                "secret_assignment",
                "secret_url_parameter",
            }:
                replacement = rf"\g<1>{REDACTED}"
            else:
                replacement = REDACTED
            redacted, replacements = pattern.subn(replacement, redacted)
            if replacements:
                count += replacements
                categories.add(category)
        return RedactionResult(redacted, count, tuple(sorted(categories)))

    def redact_text(self, text: str) -> str:
        return self.redact_text_result(text).value

    def redact_data_result(self, value: Any) -> RedactionResult[Any]:
        categories: set[str] = set()

        def visit(item: Any, key: str | None = None) -> tuple[Any, int]:
            if key is not None and _is_sensitive_key(key):
                categories.add("sensitive_field")
                return REDACTED, 1
            if isinstance(item, BaseModel):
                return visit(item.model_dump(mode="python"))
            if isinstance(item, Mapping):
                output: dict[str, Any] = {}
                replacements = 0
                for child_key, child_value in item.items():
                    rendered_key = str(child_key)
                    key_result = self.redact_text_result(rendered_key)
                    categories.update(key_result.categories)
                    output[key_result.value], child_count = visit(child_value, rendered_key)
                    replacements += child_count + key_result.replacements
                return output, replacements
            if isinstance(item, tuple):
                values: list[Any] = []
                replacements = 0
                for child in item:
                    redacted_child, child_count = visit(child)
                    values.append(redacted_child)
                    replacements += child_count
                return tuple(values), replacements
            if isinstance(item, Sequence) and not isinstance(item, (str, bytes, bytearray)):
                values = []
                replacements = 0
                for child in item:
                    redacted_child, child_count = visit(child)
                    values.append(redacted_child)
                    replacements += child_count
                return values, replacements
            if isinstance(item, str):
                result = self.redact_text_result(item)
                categories.update(result.categories)
                return result.value, result.replacements
            if isinstance(item, (bytes, bytearray)):
                result = self.redact_text_result(bytes(item).decode("utf-8", errors="replace"))
                categories.update(result.categories)
                return result.value, result.replacements
            if isinstance(item, Path):
                return visit(str(item))
            if isinstance(item, Enum):
                return visit(item.value)
            if is_dataclass(item) and not isinstance(item, type):
                return visit(asdict(item))
            if item is None or isinstance(item, (bool, int, float)):
                return item, 0
            # Log payloads are untrusted. Rendering an unknown object and then
            # redacting the rendering is safer than handing a non-JSON object to a
            # serializer (or persisting its raw repr).
            return visit(repr(item))

        redacted, replacements = visit(value)
        return RedactionResult(redacted, replacements, tuple(sorted(categories)))

    def redact_data(self, value: Any) -> Any:
        return self.redact_data_result(value).value


def _is_sensitive_key(key: str) -> bool:
    normalized = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", key).lower()
    if normalized in {
        "authorization",
        "proxy-authorization",
        "openai_api_key",
        "api_key",
        "token",
        "secret",
        "password",
        "cookie",
        "set-cookie",
    }:
        return True
    return _SENSITIVE_KEY.search(normalized) is not None


_DEFAULT_REDACTOR = SecretRedactor()


def redact_text(text: str, *, secrets: Iterable[str] = ()) -> str:
    return (SecretRedactor(secrets) if secrets else _DEFAULT_REDACTOR).redact_text(text)


def redact_data(value: Any, *, secrets: Iterable[str] = ()) -> Any:
    return (SecretRedactor(secrets) if secrets else _DEFAULT_REDACTOR).redact_data(value)


def contains_secret(text: str, *, secrets: Iterable[str] = ()) -> bool:
    return (
        (SecretRedactor(secrets) if secrets else _DEFAULT_REDACTOR).redact_text_result(text).changed
    )


def sanitized_environment(
    environment: Mapping[str, str] | None = None,
    *,
    allow_sensitive_names: Iterable[str] = (),
) -> dict[str, str]:
    """Copy an environment while withholding credential-like variables.

    ``allow_sensitive_names`` is intentionally name-based and should be used only for a
    subprocess integration whose explicit contract requires that named credential. ASCEND's
    Codex backend deliberately leaves this empty and reuses Codex-managed saved login state.
    """

    source = os.environ if environment is None else environment
    allowed = {name.upper() for name in allow_sensitive_names}
    return {
        key: value
        for key, value in source.items()
        if _SENSITIVE_ENVIRONMENT_NAME.search(key) is None or key.upper() in allowed
    }


__all__ = [
    "REDACTED",
    "RedactionResult",
    "SecretRedactor",
    "contains_secret",
    "redact_data",
    "redact_text",
    "sanitized_environment",
]
