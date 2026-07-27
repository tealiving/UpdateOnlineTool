"""UOT release 标识与受控路径合同。"""

from __future__ import annotations

import re
import unicodedata
from pathlib import Path, PurePosixPath

from update_online_tool.errors import UpdateError, UpdateErrorCode

_VERSION_RE = re.compile(r"^[0-9A-Za-z](?:[0-9A-Za-z._+-]{0,126}[0-9A-Za-z])?$")
_WINDOWS_INVALID_CHARACTERS = frozenset('<>:"|?*')
_WINDOWS_RESERVED_NAMES = frozenset(
    {
        "CON",
        "CONIN$",
        "CONOUT$",
        "PRN",
        "AUX",
        "NUL",
        *(f"COM{index}" for index in range(1, 10)),
        *(f"LPT{index}" for index in range(1, 10)),
        *(f"COM{index}" for index in "¹²³"),
        *(f"LPT{index}" for index in "¹²³"),
    }
)
_RELEASE_PLATFORMS = frozenset({"windows", "macos", "linux"})
_RELEASE_PLATFORM_ALIASES = {
    "win": "windows",
    "win32": "windows",
    "darwin": "macos",
    "mac": "macos",
    "osx": "macos",
}


def validate_release_component(
    value: object,
    field_name: str,
    *,
    error_code: UpdateErrorCode = UpdateErrorCode.SETTINGS_INVALID,
    maximum_length: int = 128,
    length_error_code: UpdateErrorCode | None = None,
) -> str:
    """将发布字段验证为一个跨平台安全的文件名段。"""
    raw_text = value if isinstance(value, str) else ""
    text = unicodedata.normalize("NFC", raw_text)
    if not text.strip():
        raise UpdateError(
            error_code, f"{field_name} must be a non-empty path component"
        )
    if text != text.strip():
        raise UpdateError(
            error_code, f"{field_name} must not start or end with whitespace"
        )
    if text in {".", ".."} or "/" in text or "\\" in text:
        raise UpdateError(error_code, f"{field_name} must be a single path component")
    if text.endswith((" ", ".")):
        raise UpdateError(error_code, f"{field_name} must not end with a space or dot")
    if any(ord(character) < 32 or ord(character) == 127 for character in text):
        raise UpdateError(
            error_code, f"{field_name} must not contain control characters"
        )
    if any(character in _WINDOWS_INVALID_CHARACTERS for character in text):
        raise UpdateError(
            error_code, f"{field_name} contains a character that is invalid on Windows"
        )
    try:
        utf8_bytes = len(text.encode("utf-8"))
        utf16_units = len(text.encode("utf-16-le")) // 2
    except UnicodeEncodeError as exc:
        raise UpdateError(
            error_code, f"{field_name} must contain valid Unicode text"
        ) from exc
    if (
        len(text) > maximum_length
        or utf8_bytes > maximum_length
        or utf16_units > maximum_length
    ):
        raise UpdateError(
            length_error_code or error_code,
            f"{field_name} must be at most {maximum_length} portable filename units",
        )
    reserved_stem = text.split(".", 1)[0].upper()
    if reserved_stem in _WINDOWS_RESERVED_NAMES:
        raise UpdateError(error_code, f"{field_name} uses a Windows reserved name")
    return text


def validate_release_platform(
    value: object,
    *,
    error_code: UpdateErrorCode = UpdateErrorCode.SETTINGS_INVALID,
    allow_empty: bool = False,
    allow_aliases: bool = False,
) -> str:
    """验证 release 目标平台的规范三值合同。"""
    text = value.strip() if isinstance(value, str) else ""
    if not text and allow_empty:
        return ""
    if allow_aliases:
        text = _RELEASE_PLATFORM_ALIASES.get(text.lower(), text.lower())
    normalized = validate_release_component(
        text,
        "platform",
        error_code=error_code,
    )
    if normalized not in _RELEASE_PLATFORMS:
        raise UpdateError(
            error_code,
            "platform must be one of windows, macos, or linux",
        )
    return normalized


def normalize_release_version(
    value: object,
    *,
    error_code: UpdateErrorCode = UpdateErrorCode.SETTINGS_INVALID,
) -> str:
    """验证并返回可安全用于 ``releases/<version>`` 的版本文本。"""
    text = value.strip() if isinstance(value, str) else ""
    normalized = text[1:] if text.lower().startswith("v") else text
    normalized = validate_release_component(
        normalized,
        "version",
        error_code=error_code,
        maximum_length=128,
    )
    if not _VERSION_RE.fullmatch(normalized):
        raise UpdateError(
            error_code,
            "version must contain only letters, digits, dot, underscore, plus, or hyphen",
        )
    return normalized


def validate_release_relative_path(
    value: object,
    field_name: str,
    *,
    error_code: UpdateErrorCode = UpdateErrorCode.SETTINGS_INVALID,
    maximum_length: int = 1024,
) -> str:
    """验证跨平台安全的正斜杠相对路径。"""
    raw_text = value if isinstance(value, str) else ""
    text = unicodedata.normalize("NFC", raw_text)
    if not text or "\\" in text:
        raise UpdateError(
            error_code, f"{field_name} must be a non-empty forward-slash relative path"
        )
    if len(text) > maximum_length:
        raise UpdateError(
            error_code, f"{field_name} must be at most {maximum_length} characters"
        )
    raw_parts = text.split("/")
    if any(part in {"", ".", ".."} for part in raw_parts):
        raise UpdateError(error_code, f"{field_name} must stay inside its managed root")
    path = PurePosixPath(text)
    if path.is_absolute():
        raise UpdateError(error_code, f"{field_name} must stay inside its managed root")
    normalized_parts = tuple(
        validate_release_component(
            part,
            field_name,
            error_code=error_code,
            maximum_length=255,
        )
        for part in path.parts
    )
    return PurePosixPath(*normalized_parts).as_posix()


def ensure_path_within(
    root: Path,
    candidate: Path,
    field_name: str,
    *,
    error_code: UpdateErrorCode = UpdateErrorCode.SETTINGS_INVALID,
) -> Path:
    """返回解析后的受控路径，并拒绝逃出指定根目录。"""
    try:
        resolved_root = Path(root).resolve(strict=False)
        resolved_candidate = Path(candidate).resolve(strict=False)
        resolved_candidate.relative_to(resolved_root)
    except (OSError, RuntimeError, ValueError) as exc:
        raise UpdateError(
            error_code, f"{field_name} escapes its managed root: {candidate}"
        ) from exc
    return resolved_candidate
