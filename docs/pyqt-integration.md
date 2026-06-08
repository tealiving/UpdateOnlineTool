# PyQt Integration Guide

The GUI project owns UI only:

- update button
- update dialog
- progress bar
- cancel button
- QThread worker
- user-facing messages

`update_online_tool` owns backend update behavior:

- read NAS manifest
- decide update availability
- copy package
- verify size and sha256
- write pending manifest
- launch standalone updater

GUI projects should call SDK methods from worker threads. The SDK does not import PyQt.

```python
from update_online_tool import UpdateService


class PrepareUpdateWorker:
    """PyQt worker example.

    :param service: 在线升级服务。
    :param manifest: 检查更新返回的 manifest。
    :param download_dir: 本地下载目录。
    :return: None
    """

    def __init__(self, service: UpdateService, manifest, download_dir):
        """保存 worker 参数。

        :param service: 在线升级服务。
        :param manifest: 检查更新返回的 manifest。
        :param download_dir: 本地下载目录。
        :return: None
        """
        self._service = service
        self._manifest = manifest
        self._download_dir = download_dir

    def run(self):
        """在线程中准备升级包。

        :return: 已准备升级包。
        """
        return self._service.prepare(
            self._manifest,
            self._download_dir,
            progress=self._emit_progress,
        )

    def _emit_progress(self, copied_bytes: int, total_bytes: int) -> None:
        """把 SDK 字节进度转换为 GUI 进度。

        :param copied_bytes: 已复制字节数。
        :param total_bytes: 总字节数。
        :return: None
        """
        percent = int(copied_bytes * 100 / total_bytes) if total_bytes else 0
        self.progress.emit(percent)
```

Suggested GUI boundary:

- `check` button calls `UpdateService.check()` in a lightweight worker.
- `update` button calls `UpdateService.prepare()` in a cancellable worker.
- after prepare completes, GUI calls `UpdateService.launch()` and exits the app.
- GUI displays `UpdateError.code.value` and `UpdateError.message` instead of parsing exception text.
