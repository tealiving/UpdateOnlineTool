"""UOT 安装根解析与诊断工具。"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from update_online_tool.errors import UpdateError, UpdateErrorCode


@dataclass(frozen=True)
class InstallRootResolution:
    """安装根解析结果。

    :param requested: 调用方传入的路径
    :param normalized: UOT 标准安装根路径
    :param looked_like_release_dir: 传入路径是否形如 releases/<version>
    :param normalized_from_release_dir: 是否从 release 目录自动归一化
    :param suggested_install_root: 当传入 release 目录时推断出的安装根
    :return: None
    """

    requested: Path
    normalized: Path
    looked_like_release_dir: bool
    normalized_from_release_dir: bool
    suggested_install_root: Path | None

    def to_payload(self) -> dict[str, object]:
        """转换为诊断 JSON 负载。

        :return: JSON 兼容字典
        """
        return {
            "requested_install_root": str(self.requested),
            "normalized_install_root": str(self.normalized),
            "looked_like_release_dir": self.looked_like_release_dir,
            "install_root_normalized": self.normalized_from_release_dir,
            "suggested_install_root": str(self.suggested_install_root) if self.suggested_install_root is not None else "",
        }


def resolve_install_root(path: Path) -> InstallRootResolution:
    """解析调用方传入的安装根路径。

    :param path: 调用方传入的安装根或误传的 release 目录
    :return: 安装根解析结果
    """
    requested = Path(path)
    if (requested / "current.json").is_file():
        return InstallRootResolution(
            requested=requested,
            normalized=requested,
            looked_like_release_dir=False,
            normalized_from_release_dir=False,
            suggested_install_root=None,
        )
    release_parent = _release_dir_parent(requested)
    if release_parent is None:
        return InstallRootResolution(
            requested=requested,
            normalized=requested,
            looked_like_release_dir=False,
            normalized_from_release_dir=False,
            suggested_install_root=None,
        )
    if (release_parent / "current.json").is_file():
        return InstallRootResolution(
            requested=requested,
            normalized=release_parent,
            looked_like_release_dir=True,
            normalized_from_release_dir=True,
            suggested_install_root=release_parent,
        )
    raise UpdateError(UpdateErrorCode.MANIFEST_NOT_FOUND, release_dir_install_root_message(requested, release_parent))


def normalize_install_root(path: Path) -> Path:
    """把误传的 release 目录归一化为 UOT 安装根。

    :param path: 调用方传入路径
    :return: 标准安装根路径
    """
    return resolve_install_root(path).normalized


def missing_current_json_message(install_root: Path) -> str:
    """生成 current.json 缺失诊断消息。

    :param install_root: 安装根路径
    :return: 错误消息
    """
    root = Path(install_root)
    release_parent = _release_dir_parent(root)
    if release_parent is not None:
        return release_dir_install_root_message(root, release_parent)
    return f"current.json not found: {root / 'current.json'}"


def missing_updater_message(install_root: Path, updater: Path) -> str:
    """生成 updater 缺失诊断消息。

    :param install_root: 安装根路径
    :param updater: 已解析的 updater 路径
    :return: 错误消息
    """
    root = Path(install_root)
    release_parent = _release_dir_parent(root)
    if release_parent is None:
        return f"updater not found: {updater}"
    expected_root = release_parent
    return (
        "updater not found under install root: {root}. "
        "The path looks like a version release directory. "
        "Use the UOT install root instead: {expected}. "
        "Expected updater directory: {updater_dir}"
    ).format(root=root, expected=expected_root, updater_dir=expected_root / "updater")


def release_dir_install_root_message(path: Path, suggested_install_root: Path) -> str:
    """生成误传 release 目录时的安装根提示。

    :param path: 误传的 release 目录
    :param suggested_install_root: 推断出的安装根
    :return: 错误消息
    """
    return (
        "current.json not found under install root: {path}. "
        "The path looks like a version release directory. "
        "Use the UOT install root instead: {expected}"
    ).format(path=Path(path), expected=Path(suggested_install_root))


def _release_dir_parent(path: Path) -> Path | None:
    """识别 releases/<version> 形态并返回安装根。

    :param path: 待识别路径
    :return: 安装根候选；不是 release 目录时返回 None
    """
    candidate = Path(path)
    if candidate.parent.name != "releases":
        return None
    if not candidate.name:
        return None
    return candidate.parent.parent
