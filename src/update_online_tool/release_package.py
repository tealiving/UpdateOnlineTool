"""Release ZIP 的跨平台规划与安全解压。"""

from __future__ import annotations

import hashlib
import shutil
import stat
import sys
import unicodedata
import zipfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import BinaryIO

from update_online_tool.errors import UpdateError, UpdateErrorCode
from update_online_tool.release_identity import (
    validate_release_component,
    validate_release_platform,
    validate_release_relative_path,
)

_MAX_COMPONENT_UNITS = 255
_MAX_PORTABLE_RELATIVE_BYTES = 1024
_WINDOWS_PATH_UNITS = 259
_MACOS_PATH_BYTES = 1023
_LINUX_PATH_BYTES = 4095
_MAX_ARCHIVE_MEMBERS = 100_000
_MAX_MEMBER_UNCOMPRESSED_BYTES = 4 * 1024 * 1024 * 1024
_MAX_TOTAL_UNCOMPRESSED_BYTES = 8 * 1024 * 1024 * 1024
_MAX_COMPRESSION_RATIO = 1_000
_COMPRESSION_RATIO_MINIMUM_BYTES = 1024 * 1024


@dataclass(frozen=True)
class ReleasePackageMember:
    """已验证的 ZIP 成员。"""

    archive_name: str
    relative_path: str
    parts: tuple[str, ...]
    kind: str
    mode: int
    crc: int
    file_size: int
    compress_size: int
    external_attr: int
    link_target: str | None = None


@dataclass(frozen=True)
class ReleasePackagePlan:
    """一次生成、供 dry-run 与真实解压共同使用的不可变计划。"""

    package_path: Path
    platform: str
    extraction_root: Path | None
    members: tuple[ReleasePackageMember, ...]
    expected_size: int | None = None
    expected_sha256: str = ""

    @classmethod
    def from_zip(
        cls,
        package_path: Path,
        *,
        platform: str = "",
        extraction_root: Path | None = None,
        expected_size: int | None = None,
        expected_sha256: str = "",
    ) -> "ReleasePackagePlan":
        """读取 ZIP 中央目录并完整验证可移植布局。"""
        package = Path(package_path)
        normalized_platform = _target_platform(platform)
        planned: list[ReleasePackageMember] = []
        path_types: dict[str, tuple[str, str, bool]] = {}
        total_uncompressed_bytes = 0
        try:
            with package.open("rb") as package_file:
                _verify_package_file(
                    package_file,
                    expected_size=expected_size,
                    expected_sha256=expected_sha256,
                )
                with zipfile.ZipFile(package_file) as archive:
                    archive_members = archive.infolist()
                    if not archive_members:
                        raise UpdateError(
                            UpdateErrorCode.PACKAGE_LAYOUT_INVALID,
                            "release package must contain at least one member",
                        )
                    if len(archive_members) > _MAX_ARCHIVE_MEMBERS:
                        raise UpdateError(
                            UpdateErrorCode.PACKAGE_LAYOUT_INVALID,
                            f"release package exceeds {_MAX_ARCHIVE_MEMBERS} members",
                        )
                    for member in archive_members:
                        total_uncompressed_bytes += member.file_size
                        if total_uncompressed_bytes > _MAX_TOTAL_UNCOMPRESSED_BYTES:
                            raise UpdateError(
                                UpdateErrorCode.PACKAGE_LAYOUT_INVALID,
                                "release package exceeds total uncompressed size budget",
                            )
                        planned_member = _plan_member(
                            archive,
                            member,
                            platform=normalized_platform,
                            extraction_root=extraction_root,
                        )
                        _register_portable_path(path_types, planned_member)
                        planned.append(planned_member)
        except zipfile.BadZipFile as exc:
            raise UpdateError(
                UpdateErrorCode.MANIFEST_INVALID,
                f"package is not a valid zip: {package}",
            ) from exc
        except FileNotFoundError as exc:
            raise UpdateError(
                UpdateErrorCode.PACKAGE_NOT_FOUND,
                f"package not found: {package}",
            ) from exc
        return cls(
            package_path=package,
            platform=normalized_platform,
            extraction_root=Path(extraction_root)
            if extraction_root is not None
            else None,
            members=tuple(planned),
            expected_size=expected_size,
            expected_sha256=str(expected_sha256 or "").lower(),
        )

    def require_entry(self, entry_name: str) -> None:
        """确认计划包含 release 入口或有效 macOS app bundle。"""
        normalized_entry = validate_release_relative_path(entry_name, "release entry")
        members_by_name = {member.relative_path: member for member in self.members}
        entry = members_by_name.get(normalized_entry)
        if entry is not None and entry.kind == "file":
            return
        if normalized_entry.endswith(".app"):
            macos_prefix = f"{normalized_entry}/Contents/MacOS/"
            if any(
                member.kind == "file"
                and member.relative_path.startswith(macos_prefix)
                and "/" not in member.relative_path[len(macos_prefix) :]
                for member in self.members
            ):
                return
        raise UpdateError(
            UpdateErrorCode.SETTINGS_INVALID,
            f"release entry not found in package: {entry_name}",
        )

    def extract(self, target_dir: Path) -> None:
        """严格按已验证计划解压，不重新解释成员路径。"""
        target = Path(target_dir)
        if self.extraction_root is not None and target != self.extraction_root:
            raise UpdateError(
                UpdateErrorCode.SETTINGS_INVALID,
                "package plan extraction root changed after validation",
            )
        try:
            with self.package_path.open("rb") as package_file:
                _verify_package_file(
                    package_file,
                    expected_size=self.expected_size,
                    expected_sha256=self.expected_sha256,
                )
                target.mkdir(parents=True, exist_ok=False)
                with zipfile.ZipFile(package_file) as archive:
                    archive_members = archive.infolist()
                    if len(archive_members) != len(self.members):
                        raise UpdateError(
                            UpdateErrorCode.PACKAGE_LAYOUT_INVALID,
                            "package changed after layout validation",
                        )
                    for planned, member in zip(
                        self.members, archive_members, strict=True
                    ):
                        if (
                            planned.archive_name != member.filename
                            or planned.crc != member.CRC
                            or planned.file_size != member.file_size
                            or planned.compress_size != member.compress_size
                            or planned.external_attr != member.external_attr
                        ):
                            raise UpdateError(
                                UpdateErrorCode.PACKAGE_LAYOUT_INVALID,
                                "package changed after layout validation",
                            )
                        extracted_path = target.joinpath(*planned.parts)
                        if planned.kind == "directory":
                            extracted_path.mkdir(parents=True, exist_ok=True)
                            if planned.mode:
                                extracted_path.chmod(planned.mode)
                            continue
                        if planned.kind == "symlink":
                            _extract_symlink(planned, extracted_path)
                            continue
                        extracted_path.parent.mkdir(parents=True, exist_ok=True)
                        with (
                            archive.open(member) as source_file,
                            extracted_path.open("wb") as target_file,
                        ):
                            shutil.copyfileobj(source_file, target_file)
                        if planned.mode:
                            extracted_path.chmod(planned.mode)
                _verify_package_file(
                    package_file,
                    expected_size=self.expected_size,
                    expected_sha256=self.expected_sha256,
                )
        except zipfile.BadZipFile as exc:
            raise UpdateError(
                UpdateErrorCode.PACKAGE_LAYOUT_INVALID,
                f"package changed or became invalid after layout validation: {self.package_path}",
            ) from exc


def _plan_member(
    archive: zipfile.ZipFile,
    member: zipfile.ZipInfo,
    *,
    platform: str,
    extraction_root: Path | None,
) -> ReleasePackageMember:
    """将单个中央目录成员转换为受控计划项。"""
    if member.file_size > _MAX_MEMBER_UNCOMPRESSED_BYTES:
        raise UpdateError(
            UpdateErrorCode.PACKAGE_LAYOUT_INVALID,
            f"zip member exceeds uncompressed size budget: {member.filename}",
        )
    if (
        member.file_size >= _COMPRESSION_RATIO_MINIMUM_BYTES
        and member.file_size > max(1, member.compress_size) * _MAX_COMPRESSION_RATIO
    ):
        raise UpdateError(
            UpdateErrorCode.PACKAGE_LAYOUT_INVALID,
            f"zip member compression ratio exceeds {_MAX_COMPRESSION_RATIO}: {member.filename}",
        )
    archive_name = str(member.filename or "")
    if "\\" in archive_name:
        raise UpdateError(
            UpdateErrorCode.MANIFEST_INVALID, f"unsafe zip member: {archive_name}"
        )
    raw_path = (
        archive_name[:-1]
        if member.is_dir() and archive_name.endswith("/")
        else archive_name
    )
    if not raw_path:
        raise UpdateError(
            UpdateErrorCode.PACKAGE_LAYOUT_INVALID,
            f"empty zip member path: {archive_name}",
        )
    normalized_path = unicodedata.normalize("NFC", raw_path)
    if any(part in {"", ".", ".."} for part in normalized_path.split("/")):
        raise UpdateError(
            UpdateErrorCode.MANIFEST_INVALID, f"unsafe zip member: {archive_name}"
        )
    pure_path = PurePosixPath(normalized_path)
    if pure_path.is_absolute():
        raise UpdateError(
            UpdateErrorCode.MANIFEST_INVALID, f"unsafe zip member: {archive_name}"
        )
    parts = tuple(pure_path.parts)
    for part in parts:
        validate_release_component(
            part,
            f"zip member {archive_name}",
            error_code=UpdateErrorCode.PACKAGE_LAYOUT_INVALID,
            maximum_length=_MAX_COMPONENT_UNITS,
            length_error_code=UpdateErrorCode.PACKAGE_PATH_TOO_LONG,
        )
    relative_path = PurePosixPath(*parts).as_posix()
    if len(relative_path.encode("utf-8")) > _MAX_PORTABLE_RELATIVE_BYTES:
        raise UpdateError(
            UpdateErrorCode.PACKAGE_PATH_TOO_LONG,
            f"zip member relative path exceeds {_MAX_PORTABLE_RELATIVE_BYTES} UTF-8 bytes: {archive_name}",
        )
    if extraction_root is not None:
        _validate_destination_budget(
            Path(extraction_root).joinpath(*parts), archive_name, platform
        )
    mode = member.external_attr >> 16
    kind = (
        "directory" if member.is_dir() else "symlink" if stat.S_ISLNK(mode) else "file"
    )
    link_target = (
        _validated_symlink_target(archive, member, parts) if kind == "symlink" else None
    )
    return ReleasePackageMember(
        archive_name=archive_name,
        relative_path=relative_path,
        parts=parts,
        kind=kind,
        mode=mode,
        crc=member.CRC,
        file_size=member.file_size,
        compress_size=member.compress_size,
        external_attr=member.external_attr,
        link_target=link_target,
    )


def _validate_destination_budget(
    destination: Path, archive_name: str, platform: str
) -> None:
    """在创建目录前验证目标平台完整路径预算。"""
    text = str(destination)
    if platform == "windows":
        units = len(text.encode("utf-16-le")) // 2
        if units > _WINDOWS_PATH_UNITS:
            raise UpdateError(
                UpdateErrorCode.PACKAGE_PATH_TOO_LONG,
                f"Windows extraction path exceeds {_WINDOWS_PATH_UNITS} UTF-16 units: {archive_name}",
            )
        return
    encoded_length = len(text.encode("utf-8"))
    limit = _MACOS_PATH_BYTES if platform == "macos" else _LINUX_PATH_BYTES
    if encoded_length > limit:
        raise UpdateError(
            UpdateErrorCode.PACKAGE_PATH_TOO_LONG,
            f"{platform or 'host'} extraction path exceeds {limit} UTF-8 bytes: {archive_name}",
        )


def _target_platform(platform: str) -> str:
    """解析目标平台；旧 manifest 未声明时使用当前宿主预算。"""
    normalized = validate_release_platform(
        platform,
        error_code=UpdateErrorCode.MANIFEST_INVALID,
        allow_empty=True,
    )
    if normalized:
        return normalized
    if sys.platform.startswith("win"):
        return "windows"
    if sys.platform == "darwin":
        return "macos"
    return "linux"


def _portable_key(parts: tuple[str, ...]) -> str:
    """生成大小写不敏感且 Unicode 规范等价的冲突键。"""
    return "/".join(unicodedata.normalize("NFC", part).casefold() for part in parts)


def _register_portable_path(
    path_types: dict[str, tuple[str, str, bool]],
    member: ReleasePackageMember,
) -> None:
    """拒绝重复、大小写/Unicode 冲突及文件-目录结构冲突。"""
    for depth in range(1, len(member.parts)):
        parent_parts = member.parts[:depth]
        parent_key = _portable_key(parent_parts)
        existing = path_types.get(parent_key)
        if existing is not None and existing[0] != "directory":
            raise _collision_error(existing[1], member.archive_name)
        path_types.setdefault(parent_key, ("directory", "/".join(parent_parts), False))

    key = _portable_key(member.parts)
    existing = path_types.get(key)
    if existing is not None:
        if (
            existing[0] == "directory"
            and not existing[2]
            and member.kind == "directory"
        ):
            path_types[key] = ("directory", member.archive_name, True)
            return
        raise _collision_error(existing[1], member.archive_name)
    path_types[key] = (member.kind, member.archive_name, True)


def _collision_error(first: str, second: str) -> UpdateError:
    """构造稳定的跨平台冲突错误。"""
    return UpdateError(
        UpdateErrorCode.PACKAGE_LAYOUT_INVALID,
        f"portable path collision between zip members: {first!r} and {second!r}",
    )


def _validated_symlink_target(
    archive: zipfile.ZipFile,
    member: zipfile.ZipInfo,
    member_parts: tuple[str, ...],
) -> str:
    """在计划阶段读取并验证 symlink 目标。"""
    if member.file_size > _MAX_PORTABLE_RELATIVE_BYTES:
        raise UpdateError(
            UpdateErrorCode.PACKAGE_PATH_TOO_LONG,
            f"symlink target exceeds {_MAX_PORTABLE_RELATIVE_BYTES} UTF-8 bytes: {member.filename}",
        )
    try:
        link_target = unicodedata.normalize("NFC", archive.read(member).decode("utf-8"))
    except UnicodeDecodeError as exc:
        raise UpdateError(
            UpdateErrorCode.PACKAGE_LAYOUT_INVALID,
            f"symlink target must be UTF-8: {member.filename}",
        ) from exc
    if not link_target:
        raise UpdateError(
            UpdateErrorCode.PACKAGE_LAYOUT_INVALID,
            f"empty symlink target: {member.filename}",
        )
    if len(link_target.encode("utf-8")) > _MAX_PORTABLE_RELATIVE_BYTES:
        raise UpdateError(
            UpdateErrorCode.PACKAGE_PATH_TOO_LONG,
            f"symlink target exceeds {_MAX_PORTABLE_RELATIVE_BYTES} UTF-8 bytes: {member.filename}",
        )
    if "\\" in link_target:
        raise UpdateError(
            UpdateErrorCode.PACKAGE_LAYOUT_INVALID,
            f"unsafe symlink target: {member.filename}",
        )
    link_target_path = PurePosixPath(link_target)
    if link_target_path.is_absolute():
        raise UpdateError(
            UpdateErrorCode.PACKAGE_LAYOUT_INVALID,
            f"unsafe symlink target: {member.filename}",
        )
    resolved_parts = list(member_parts[:-1])
    for part in link_target_path.parts:
        if part in {"", "."}:
            continue
        if part == "..":
            if not resolved_parts:
                raise UpdateError(
                    UpdateErrorCode.PACKAGE_LAYOUT_INVALID,
                    f"unsafe symlink target: {member.filename}",
                )
            resolved_parts.pop()
            continue
        validate_release_component(
            part,
            f"symlink target for {member.filename}",
            error_code=UpdateErrorCode.PACKAGE_LAYOUT_INVALID,
            maximum_length=_MAX_COMPONENT_UNITS,
        )
        resolved_parts.append(part)
    return link_target


def _verify_package_file(
    package_file: BinaryIO,
    *,
    expected_size: int | None,
    expected_sha256: str,
) -> None:
    """针对同一打开文件验证 manifest 大小与 SHA-256。"""
    if expected_size is None and not expected_sha256:
        package_file.seek(0)
        return
    package_file.seek(0, 2)
    actual_size = package_file.tell()
    if expected_size is not None and actual_size != expected_size:
        raise UpdateError(
            UpdateErrorCode.PACKAGE_SIZE_MISMATCH,
            f"package.size {expected_size} != actual {actual_size}",
        )
    package_file.seek(0)
    digest = hashlib.sha256()
    for chunk in iter(lambda: package_file.read(1024 * 1024), b""):
        digest.update(chunk)
    actual_sha256 = digest.hexdigest()
    if expected_sha256 and actual_sha256 != expected_sha256.lower():
        raise UpdateError(
            UpdateErrorCode.PACKAGE_HASH_MISMATCH,
            f"package.sha256 {expected_sha256.lower()} != actual {actual_sha256}",
        )
    package_file.seek(0)


def _extract_symlink(
    member: ReleasePackageMember,
    extracted_path: Path,
) -> None:
    """按计划恢复 POSIX symlink。"""
    if member.link_target is None:
        raise UpdateError(
            UpdateErrorCode.PACKAGE_LAYOUT_INVALID,
            f"symlink target missing from package plan: {member.archive_name}",
        )
    extracted_path.parent.mkdir(parents=True, exist_ok=True)
    extracted_path.symlink_to(member.link_target)
