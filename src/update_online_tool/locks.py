"""安装根运行时锁。"""

from __future__ import annotations

import json
import os
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from update_online_tool.errors import UpdateError, UpdateErrorCode


@contextmanager
def runtime_lock(install_root: Path, *, action: str, dry_run: bool = False) -> Iterator[None]:
    """用 update.lock 防止同一安装根并发更新或切换。"""
    if dry_run:
        yield
        return
    root = Path(install_root)
    root.mkdir(parents=True, exist_ok=True)
    lock_path = root / "update.lock"
    flags = os.O_CREAT | os.O_EXCL | os.O_WRONLY
    try:
        fd = os.open(lock_path, flags)
    except FileExistsError as exc:
        raise UpdateError(UpdateErrorCode.UPDATE_LOCKED, f"update lock exists: {lock_path}") from exc
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as lock_file:
            lock_file.write(json.dumps({"pid": os.getpid(), "action": action}, ensure_ascii=False) + "\n")
        yield
    finally:
        try:
            lock_path.unlink()
        except FileNotFoundError:
            return
