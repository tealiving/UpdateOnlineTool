"""UpdateOnlineTool 命令行入口。"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sys
import zipfile
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from update_online_tool.diagnostics import collect_diagnostics, write_diagnostic_archive
from update_online_tool.errors import UpdateError, UpdateErrorCode
from update_online_tool.install_root import normalize_install_root
from update_online_tool.installed import list_installed_versions, migrate_install_root
from update_online_tool.manifest import UpdateManifest
from update_online_tool.migration_package import (
    verify_migration_package,
    write_migration_package_template,
)
from update_online_tool.nas import NasReleaseSource
from update_online_tool.pyinstaller_assembly import (
    assemble_pyinstaller_release,
    default_pyinstaller_assembly_config,
    write_agent_pyinstaller_spec,
    write_bootstrap_pyinstaller_spec,
    write_bridge_pyinstaller_spec,
    write_updater_pyinstaller_spec,
)
from update_online_tool.release_assembly import (
    ReleaseAssemblyConfig,
    assemble_release_layout,
    create_release_package,
)
from update_online_tool.release_contract import (
    RELEASE_CONTRACT_FILENAME,
    ReleaseArtifactContract,
    validate_release_artifact,
    write_release_contract,
)
from update_online_tool.release_identity import (
    normalize_release_version,
    validate_release_component,
    validate_release_platform,
)
from update_online_tool.release_package import ReleasePackagePlan
from update_online_tool.runtime import (
    apply_pending_update,
    install_prepared_package,
    launch_current,
    rollback_installation,
    switch_installed_release,
)
from update_online_tool.service import UpdateService
from update_online_tool.settings import (
    UpdateToolSettings,
    normalize_nas_root,
    user_settings_path,
)
from update_online_tool.signature import (
    derive_ed25519_public_key_pem,
    generate_ed25519_private_key_pem,
    generate_hmac_key,
    sign_manifest_payload_with_key_file,
    verify_manifest_signature_with_key_file,
)
from update_online_tool.versioning import parse_version_tuple


def main(argv: list[str] | None = None) -> int:
    """运行 CLI。

    :param argv: 命令行参数；为空时读取 sys.argv。
    :return: 进程退出码。
    """
    parser = _build_parser()
    args = parser.parse_args(argv)
    try:
        if args.command == "keygen":
            return _keygen(args)
        if args.command == "init":
            return _init(args)
        if args.command == "publish":
            return _publish(args)
        if args.command == "verify":
            return _verify(args)
        if args.command == "check":
            return _check(args)
        if args.command == "list-remote":
            return _list_remote(args)
        if args.command == "show-version":
            return _show_version(args)
        if args.command == "prepare-version":
            return _prepare_version(args)
        if args.command == "list-installed":
            return _list_installed(args)
        if args.command == "switch-installed":
            return _switch_installed(args)
        if args.command == "migrate-install-root":
            return _migrate_install_root(args)
        if args.command == "write-migration-package":
            return _write_migration_package(args)
        if args.command == "verify-migration-package":
            return _verify_migration_package(args)
        if args.command == "install-prepared":
            return _install_prepared(args)
        if args.command == "apply-update":
            return _apply_update(args)
        if args.command == "rollback":
            return _rollback(args)
        if args.command == "launch-current":
            return _launch_current(args)
        if args.command == "doctor":
            return _doctor(args)
        if args.command == "write-updater-spec":
            return _write_updater_spec(args)
        if args.command == "write-agent-spec":
            return _write_agent_spec(args)
        if args.command == "write-bootstrap-spec":
            return _write_bootstrap_spec(args)
        if args.command == "write-bridge-spec":
            return _write_bridge_spec(args)
        if args.command == "assemble-pyinstaller":
            return _assemble_pyinstaller(args)
        if args.command == "assemble-release":
            return _assemble_release(args)
        if args.command == "package-release":
            return _package_release(args)
        if args.command == "write-release-contract":
            return _write_release_contract(args)
        if args.command == "validate-release":
            return _validate_release(args)
    except UpdateError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    parser.print_help()
    return 2


def _build_parser() -> argparse.ArgumentParser:
    """构建参数解析器。

    :return: 参数解析器。
    """
    parser = argparse.ArgumentParser(prog="uot", description="NAS online updater CLI.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    keygen = subparsers.add_parser("keygen", help="Generate a manifest signing key.")
    keygen.add_argument(
        "--output", required=True, type=Path, help="Signing key output path."
    )
    keygen.add_argument(
        "--algorithm",
        default="ed25519",
        choices=["ed25519", "hmac-sha256"],
        help="Signing algorithm. Defaults to Ed25519.",
    )
    keygen.add_argument(
        "--public-output",
        default=None,
        type=Path,
        help="Public key output path for Ed25519.",
    )
    keygen.add_argument(
        "--force", action="store_true", help="Overwrite existing key file."
    )

    init = subparsers.add_parser(
        "init", help="Generate a project update-endpoint.json."
    )
    init.add_argument(
        "--app", default="", help="Application id. Defaults to current directory name."
    )
    init.add_argument("--channel", default="stable", help="Release channel.")
    init.add_argument(
        "--output",
        default="update-endpoint.json",
        type=Path,
        help="Output endpoint JSON path.",
    )
    init.add_argument(
        "--source-name", default="local-nas", help="Manifest source name."
    )
    init.add_argument("--installer-mode", default="uot_updater", help="Installer mode.")
    init.add_argument(
        "--package-url-prefix", default="uot-nas://nas", help="Package URL prefix."
    )
    init.add_argument(
        "--auth-provider", default="update_online_tool", help="Auth provider."
    )
    init.add_argument(
        "--priority", default=10, type=int, help="Manifest source priority."
    )
    init.add_argument(
        "--nas-root",
        default=None,
        help="Optional NAS root. Writes project settings when set.",
    )
    init.add_argument(
        "--settings-output",
        default=None,
        type=Path,
        help="Optional settings output path.",
    )
    init.add_argument(
        "--user-settings",
        action="store_true",
        help="Write settings to the OS user config directory.",
    )
    init.add_argument(
        "--updater-name",
        default="Updater.exe",
        help="Standalone updater executable name.",
    )
    init.add_argument(
        "--skip-nas-check",
        action="store_true",
        help="Skip NAS read/write validation during init.",
    )
    init.add_argument(
        "--force", action="store_true", help="Overwrite existing output file."
    )

    publish = subparsers.add_parser(
        "publish", help="Publish a zip package to the NAS release root."
    )
    _add_settings_arg(publish)
    publish.add_argument("--app", required=True, help="Application id.")
    publish.add_argument("--version", required=True, help="Release version.")
    publish.add_argument(
        "--package", required=True, type=Path, help="Release zip path."
    )
    publish.add_argument(
        "--release-contract",
        default=None,
        type=Path,
        help="Optional validated uot-release.json path.",
    )
    publish.add_argument(
        "--channel", default="", help="Release channel. Defaults to settings."
    )
    publish.add_argument(
        "--platform",
        default="",
        help="Optional target platform: windows, macos, or linux.",
    )
    publish.add_argument("--notes", default="", help="Release notes.")
    publish.add_argument(
        "--notes-file",
        default=None,
        type=Path,
        help="Read release notes from a text file.",
    )
    publish.add_argument(
        "--min-supported-version", default="", help="Minimum supported current version."
    )
    publish.add_argument(
        "--mandatory", action="store_true", help="Mark this update as mandatory."
    )
    publish.add_argument(
        "--published-at",
        default="",
        help="ISO timestamp. Defaults to current UTC time.",
    )
    publish.add_argument(
        "--allow-downgrade",
        action="store_true",
        help="Allow switching down to this version.",
    )
    publish.add_argument(
        "--hidden",
        action="store_true",
        help="Hide this version from normal version lists.",
    )
    publish.add_argument(
        "--requires-confirmation",
        action="store_true",
        help="Require user confirmation before install.",
    )
    publish.add_argument(
        "--rollout-percent",
        default=100,
        type=int,
        help="Rollout percentage from 0 to 100.",
    )
    publish.add_argument(
        "--data-schema-version",
        default=0,
        type=int,
        help="Application data schema version.",
    )
    publish.add_argument(
        "--sign-key",
        default=None,
        type=Path,
        help="Signing key file used to sign latest.json.",
    )
    publish.add_argument(
        "--key-id", default="default", help="Signature key id written to latest.json."
    )

    verify = subparsers.add_parser(
        "verify", help="Verify one app manifest and package."
    )
    _add_settings_arg(verify)
    verify.add_argument("--app", required=True, help="Application id.")
    verify.add_argument(
        "--channel", default="", help="Release channel. Defaults to settings."
    )
    verify.add_argument(
        "--platform",
        default="",
        help="Optional target platform: windows, macos, or linux.",
    )
    verify.add_argument(
        "--signature-key",
        default=None,
        type=Path,
        help="Key file used to verify manifest signature.",
    )

    check = subparsers.add_parser(
        "check", help="Check whether an app version has an update."
    )
    _add_settings_arg(check)
    check.add_argument("--app", required=True, help="Application id.")
    check.add_argument("--current-version", required=True, help="Current app version.")
    check.add_argument(
        "--channel", default="", help="Release channel. Defaults to settings."
    )
    check.add_argument(
        "--platform",
        default="",
        help="Optional target platform: windows, macos, or linux.",
    )
    check.add_argument("--skipped-version", default="", help="Skipped version.")
    check.add_argument(
        "--signature-key",
        default=None,
        type=Path,
        help="Key file used to verify manifest signature.",
    )

    list_remote = subparsers.add_parser(
        "list-remote", help="List published versions on the NAS release root."
    )
    _add_settings_arg(list_remote)
    list_remote.add_argument("--app", required=True, help="Application id.")
    list_remote.add_argument(
        "--channel", default="", help="Release channel. Defaults to settings."
    )
    list_remote.add_argument(
        "--platform",
        default="",
        help="Optional target platform: windows, macos, or linux.",
    )
    list_remote.add_argument(
        "--include-hidden", action="store_true", help="Include hidden versions."
    )
    list_remote.add_argument(
        "--signature-key",
        default=None,
        type=Path,
        help="Key file used to verify manifest signatures.",
    )

    show_version = subparsers.add_parser(
        "show-version", help="Print one published version manifest."
    )
    _add_settings_arg(show_version)
    show_version.add_argument("--app", required=True, help="Application id.")
    show_version.add_argument("--version", required=True, help="Release version.")
    show_version.add_argument(
        "--channel", default="", help="Release channel. Defaults to settings."
    )
    show_version.add_argument(
        "--platform",
        default="",
        help="Optional target platform: windows, macos, or linux.",
    )
    show_version.add_argument(
        "--signature-key",
        default=None,
        type=Path,
        help="Key file used to verify manifest signature.",
    )

    prepare_version = subparsers.add_parser(
        "prepare-version", help="Copy and verify one published version package."
    )
    _add_settings_arg(prepare_version)
    prepare_version.add_argument("--app", required=True, help="Application id.")
    prepare_version.add_argument("--version", required=True, help="Release version.")
    prepare_version.add_argument(
        "--download-dir",
        required=True,
        type=Path,
        help="Local package download directory.",
    )
    prepare_version.add_argument(
        "--channel", default="", help="Release channel. Defaults to settings."
    )
    prepare_version.add_argument(
        "--platform",
        default="",
        help="Optional target platform: windows, macos, or linux.",
    )
    prepare_version.add_argument(
        "--signature-key",
        default=None,
        type=Path,
        help="Key file used to verify manifest signature.",
    )

    list_installed = subparsers.add_parser(
        "list-installed", help="List releases under an assembled install root."
    )
    list_installed.add_argument(
        "--install-root", required=True, type=Path, help="Assembled install root."
    )
    list_installed.add_argument(
        "--entry-name",
        default="",
        help="Release entry name. Defaults to current.json entry.",
    )

    switch_installed = subparsers.add_parser(
        "switch-installed", help="Switch current.json to an installed release."
    )
    switch_installed.add_argument(
        "--install-root", required=True, type=Path, help="Assembled install root."
    )
    switch_installed.add_argument(
        "--version", required=True, help="Installed release version."
    )
    switch_installed.add_argument(
        "--entry-name",
        default="",
        help="Release entry name. Defaults to current.json entry.",
    )
    switch_installed.add_argument(
        "--app", default="", help="Application id. Defaults to current.json app_id."
    )
    switch_installed.add_argument(
        "--platform",
        default="",
        help="Platform. Defaults to current.json entry.platform.",
    )
    switch_installed.add_argument(
        "--wait-pid",
        default=None,
        type=int,
        help="Old application PID to wait for before switch.",
    )
    switch_installed.add_argument(
        "--wait-timeout",
        default=60.0,
        type=float,
        help="Seconds to wait for --wait-pid.",
    )
    switch_installed.add_argument(
        "--restart",
        action="store_true",
        help="Restart the current release after switch.",
    )

    migrate_install = subparsers.add_parser(
        "migrate-install-root",
        help="Migrate a flat install root to releases/current.json.",
    )
    migrate_install.add_argument(
        "--install-root", required=True, type=Path, help="Legacy install root."
    )
    migrate_install.add_argument(
        "--version", required=True, help="Version to assign to the migrated release."
    )
    migrate_install.add_argument(
        "--entry-name",
        required=True,
        help="Existing legacy entry name in the install root.",
    )
    migrate_install.add_argument("--app", required=True, help="Application id.")
    migrate_install.add_argument(
        "--platform", default="", help="Optional platform: windows, macos, or linux."
    )
    migrate_install.add_argument(
        "--force",
        action="store_true",
        help="Replace an existing target release directory.",
    )
    migrate_install.add_argument(
        "--dry-run",
        action="store_true",
        help="Show migration plan without writing files.",
    )

    migration_package = subparsers.add_parser(
        "write-migration-package",
        help="Write a legacy-client migration package template.",
    )
    migration_package.add_argument(
        "--output-dir",
        required=True,
        type=Path,
        help="Migration package output directory.",
    )
    migration_package.add_argument("--app", required=True, help="Application id.")
    migration_package.add_argument(
        "--version", required=True, help="Version assigned to the legacy install root."
    )
    migration_package.add_argument(
        "--entry-name", required=True, help="Legacy install-root entry name."
    )
    migration_package.add_argument(
        "--platform", default="", help="Optional platform: windows, macos, or linux."
    )
    migration_package.add_argument(
        "--updater-bundle",
        default=None,
        type=Path,
        help="Built updater artifact to include.",
    )
    migration_package.add_argument(
        "--settings", default=None, type=Path, help="settings.json to include."
    )
    migration_package.add_argument(
        "--endpoint", default=None, type=Path, help="update-endpoint.json to include."
    )
    migration_package.add_argument(
        "--force", action="store_true", help="Overwrite existing output directory."
    )

    verify_migration = subparsers.add_parser(
        "verify-migration-package", help="Verify a migration package template."
    )
    verify_migration.add_argument(
        "--package-dir", required=True, type=Path, help="Migration package directory."
    )

    install_prepared = subparsers.add_parser(
        "install-prepared", help="Install a verified package into releases."
    )
    install_prepared.add_argument(
        "--install-root", required=True, type=Path, help="Assembled install root."
    )
    install_prepared.add_argument(
        "--package", required=True, type=Path, help="Prepared local package zip."
    )
    install_prepared.add_argument(
        "--manifest", required=True, type=Path, help="Manifest JSON for the package."
    )
    install_prepared.add_argument(
        "--entry-name",
        default="",
        help="Release entry name. Defaults to current.json entry.",
    )
    install_prepared.add_argument(
        "--no-switch",
        action="store_true",
        help="Install release without switching current.json.",
    )
    install_prepared.add_argument(
        "--force", action="store_true", help="Replace an existing release directory."
    )
    install_prepared.add_argument(
        "--dry-run",
        action="store_true",
        help="Validate the install plan without writing files.",
    )
    install_prepared.add_argument(
        "--signature-key",
        default=None,
        type=Path,
        help="Key file used to verify manifest signature.",
    )
    install_prepared.add_argument(
        "--wait-pid",
        default=None,
        type=int,
        help="Old application PID to wait for before install.",
    )
    install_prepared.add_argument(
        "--wait-timeout",
        default=60.0,
        type=float,
        help="Seconds to wait for --wait-pid.",
    )
    install_prepared.add_argument(
        "--restart",
        action="store_true",
        help="Restart the current release after install.",
    )

    apply_update = subparsers.add_parser(
        "apply-update", help="Apply pending-update.json through the standard runtime."
    )
    apply_update.add_argument(
        "--pending", required=True, type=Path, help="pending-update.json path."
    )
    apply_update.add_argument(
        "--entry-name",
        default="",
        help="Release entry name. Defaults to current.json entry.",
    )
    apply_update.add_argument(
        "--force", action="store_true", help="Replace an existing release directory."
    )
    apply_update.add_argument(
        "--dry-run",
        action="store_true",
        help="Validate the pending update without writing files.",
    )
    apply_update.add_argument(
        "--signature-key",
        default=None,
        type=Path,
        help="Key file used to verify manifest signature.",
    )
    apply_update.add_argument(
        "--wait-pid",
        default=None,
        type=int,
        help="Old application PID to wait for before install.",
    )
    apply_update.add_argument(
        "--wait-timeout",
        default=60.0,
        type=float,
        help="Seconds to wait for --wait-pid.",
    )
    apply_update.add_argument(
        "--restart",
        action="store_true",
        help="Restart the current release after install.",
    )

    rollback = subparsers.add_parser(
        "rollback", help="Rollback current.json to previous_version."
    )
    rollback.add_argument(
        "--install-root", required=True, type=Path, help="Assembled install root."
    )
    rollback.add_argument(
        "--entry-name",
        default="",
        help="Release entry name. Defaults to current.json entry.",
    )
    rollback.add_argument(
        "--wait-pid",
        default=None,
        type=int,
        help="Old application PID to wait for before rollback.",
    )
    rollback.add_argument(
        "--wait-timeout",
        default=60.0,
        type=float,
        help="Seconds to wait for --wait-pid.",
    )
    rollback.add_argument(
        "--restart",
        action="store_true",
        help="Restart the current release after rollback.",
    )

    launch_current_parser = subparsers.add_parser(
        "launch-current", help="Launch the current release entry."
    )
    launch_current_parser.add_argument(
        "--install-root", required=True, type=Path, help="Assembled install root."
    )

    doctor = subparsers.add_parser(
        "doctor", help="Collect an install-root diagnostic report."
    )
    doctor.add_argument(
        "--install-root", required=True, type=Path, help="Assembled install root."
    )
    doctor.add_argument(
        "--entry-name",
        default="",
        help="Release entry name. Defaults to current.json entry.",
    )
    doctor.add_argument(
        "--output", default=None, type=Path, help="Optional JSON report output path."
    )
    doctor.add_argument(
        "--archive",
        default=None,
        type=Path,
        help="Optional diagnostic zip output path.",
    )

    updater_spec = subparsers.add_parser(
        "write-updater-spec", help="Write a PyInstaller spec for uot-updater."
    )
    updater_spec.add_argument(
        "--output-dir",
        required=True,
        type=Path,
        help="Directory for generated spec files.",
    )
    updater_spec.add_argument(
        "--name", default="uot-updater", help="PyInstaller executable name."
    )
    updater_spec.add_argument(
        "--onefile",
        action="store_true",
        help="Generate a onefile spec instead of onedir.",
    )
    updater_spec.add_argument(
        "--windowed", action="store_true", help="Build without a console window."
    )
    updater_spec.add_argument(
        "--force", action="store_true", help="Overwrite existing generated files."
    )

    agent_spec = subparsers.add_parser(
        "write-agent-spec", help="Write a PyInstaller spec for uot-agent."
    )
    agent_spec.add_argument(
        "--output-dir",
        required=True,
        type=Path,
        help="Directory for generated spec files.",
    )
    agent_spec.add_argument(
        "--name", default="uot-agent", help="PyInstaller executable name."
    )
    agent_spec.add_argument(
        "--onefile",
        action="store_true",
        help="Generate a onefile spec instead of onedir.",
    )
    agent_spec.add_argument(
        "--windowed", action="store_true", help="Build without a console window."
    )
    agent_spec.add_argument(
        "--force", action="store_true", help="Overwrite existing generated files."
    )

    bootstrap_spec = subparsers.add_parser(
        "write-bootstrap-spec", help="Write a PyInstaller spec for uot-bootstrap."
    )
    bootstrap_spec.add_argument(
        "--output-dir",
        required=True,
        type=Path,
        help="Directory for generated spec files.",
    )
    bootstrap_spec.add_argument(
        "--name", default="uot-bootstrap", help="PyInstaller executable name."
    )
    bootstrap_spec.add_argument(
        "--onefile",
        action="store_true",
        help="Generate a onefile spec instead of onedir.",
    )
    bootstrap_spec.add_argument(
        "--windowed", action="store_true", help="Build without a console window."
    )
    bootstrap_spec.add_argument(
        "--force", action="store_true", help="Overwrite existing generated files."
    )

    bridge_spec = subparsers.add_parser(
        "write-bridge-spec", help="Write a PyInstaller spec for uot-bridge."
    )
    bridge_spec.add_argument(
        "--output-dir",
        required=True,
        type=Path,
        help="Directory for generated spec files.",
    )
    bridge_spec.add_argument(
        "--name", default="uot-bridge", help="PyInstaller executable name."
    )
    bridge_spec.add_argument(
        "--onefile",
        action="store_true",
        help="Generate a onefile spec instead of onedir.",
    )
    bridge_spec.add_argument(
        "--windowed", action="store_true", help="Build without a console window."
    )
    bridge_spec.add_argument(
        "--force", action="store_true", help="Overwrite existing generated files."
    )

    assemble = subparsers.add_parser(
        "assemble-pyinstaller", help="Assemble PyInstaller GUI and launcher bundles."
    )
    assemble.add_argument("--version", required=True, help="Release version.")
    assemble.add_argument(
        "--dist-dir", default="dist", type=Path, help="PyInstaller dist directory."
    )
    assemble.add_argument(
        "--app", default="", help="Application id. Defaults to product name."
    )
    assemble.add_argument(
        "--product-name",
        required=True,
        help="Product name used for default bundle names.",
    )
    assemble.add_argument(
        "--platform",
        default="windows",
        choices=["windows", "macos", "linux"],
        help="Target platform.",
    )
    assemble.add_argument(
        "--entry-name",
        default="",
        help="Final stable entry name. Defaults to product name plus platform suffix.",
    )
    assemble.add_argument(
        "--release-entry-name",
        default="",
        help="Source GUI entry name inside the release bundle.",
    )
    assemble.add_argument(
        "--launcher-entry-name",
        default="",
        help="Source launcher entry name inside the launcher bundle.",
    )
    assemble.add_argument(
        "--settings", default=None, type=Path, help="Project settings.json to bundle."
    )
    assemble.add_argument(
        "--updater-bundle",
        default=None,
        type=Path,
        help="Built uot-updater onefile or onedir artifact.",
    )
    assemble.add_argument(
        "--updater-name",
        default="",
        help="Target name under install_root/updater/. Defaults to source name.",
    )
    assemble.add_argument(
        "--bootstrap-agent-mode",
        action="store_true",
        help="Keep stable Bootstrap/Agent outside release update packages.",
    )
    assemble.add_argument(
        "--agent-bundle",
        default=None,
        type=Path,
        help="Built uot-agent artifact required by --bootstrap-agent-mode.",
    )
    assemble.add_argument(
        "--agent-name",
        default="",
        help="Target name under install_root/agent/. Defaults to source name.",
    )
    assemble.add_argument(
        "--force", action="store_true", help="Overwrite existing output directories."
    )

    assemble_release = subparsers.add_parser(
        "assemble-release",
        help="Assemble a framework-agnostic release with stable Bootstrap and Agent.",
    )
    assemble_release.add_argument("--version", required=True, help="Release version.")
    assemble_release.add_argument("--app", required=True, help="Application id.")
    assemble_release.add_argument(
        "--release-dir",
        required=True,
        type=Path,
        help="Built Electron/Tauri/PyQt release directory.",
    )
    assemble_release.add_argument(
        "--release-entry", required=True, help="Entry path inside --release-dir."
    )
    assemble_release.add_argument(
        "--bootstrap-dir", required=True, type=Path, help="Stable Bootstrap directory."
    )
    assemble_release.add_argument(
        "--bootstrap-entry", required=True, help="Entry path inside --bootstrap-dir."
    )
    assemble_release.add_argument(
        "--agent-bundle", required=True, type=Path, help="Stable uot-agent bundle."
    )
    assemble_release.add_argument(
        "--install-output", required=True, type=Path, help="Target UOT install root."
    )
    assemble_release.add_argument(
        "--update-output",
        required=True,
        type=Path,
        help="Target release-only update directory.",
    )
    assemble_release.add_argument(
        "--platform",
        required=True,
        choices=["windows", "macos", "linux"],
        help="Target OS platform.",
    )
    assemble_release.add_argument(
        "--entry-path",
        required=True,
        help="Normalized entry path written to current.json.",
    )
    assemble_release.add_argument(
        "--agent-name",
        default="",
        help="Stable Agent target name. Defaults to source name.",
    )
    assemble_release.add_argument(
        "--force", action="store_true", help="Overwrite existing output directories."
    )

    package_release = subparsers.add_parser(
        "package-release",
        help="Create a Unicode-safe release zip and exclude macOS metadata.",
    )
    package_release.add_argument(
        "--source-dir",
        required=True,
        type=Path,
        help="Release-only directory to archive.",
    )
    package_release.add_argument(
        "--output", required=True, type=Path, help="Output package.zip path."
    )
    package_release.add_argument(
        "--force", action="store_true", help="Overwrite an existing package."
    )

    write_contract = subparsers.add_parser(
        "write-release-contract",
        help="Write a UOT release artifact contract after framework packaging.",
    )
    _add_release_contract_args(write_contract)
    write_contract.add_argument(
        "--force", action="store_true", help="Overwrite an existing uot-release.json."
    )

    validate_release = subparsers.add_parser(
        "validate-release",
        help="Validate an entry, runtime resources, and optional UOT release contract.",
    )
    _add_release_contract_args(validate_release)
    return parser


def _keygen(args: argparse.Namespace) -> int:
    """生成 manifest 签名密钥。"""
    output = Path(args.output)
    if output.exists() and not bool(args.force):
        raise UpdateError(
            UpdateErrorCode.SETTINGS_INVALID, f"key output already exists: {output}"
        )
    public_output = Path(args.public_output) if args.public_output is not None else None
    if public_output is not None and public_output.exists() and not bool(args.force):
        raise UpdateError(
            UpdateErrorCode.SETTINGS_INVALID,
            f"public key output already exists: {public_output}",
        )
    output.parent.mkdir(parents=True, exist_ok=True)
    if args.algorithm == "ed25519":
        output.write_text(generate_ed25519_private_key_pem(), encoding="utf-8")
        if public_output is not None:
            public_output.parent.mkdir(parents=True, exist_ok=True)
            public_output.write_text(
                derive_ed25519_public_key_pem(output), encoding="utf-8"
            )
    else:
        output.write_text(generate_hmac_key() + "\n", encoding="utf-8")
    print(f"Generated signing key: {output}")
    if public_output is not None:
        print(f"Generated public key: {public_output}")
    return 0


def _add_settings_arg(parser: argparse.ArgumentParser) -> None:
    """添加通用 settings 参数。

    :param parser: 子命令解析器。
    :return: None
    """
    parser.add_argument("--settings", default="", type=Path, help="settings.json path.")


def _init(args: argparse.Namespace) -> int:
    """生成接入方项目 update-endpoint.json。

    :param args: 命令参数。
    :return: 进程退出码。
    """
    app_id = _resolve_init_app_id(args)
    output_path = Path(args.output)
    if output_path.exists() and not args.force:
        raise UpdateError(
            UpdateErrorCode.SETTINGS_INVALID, f"output already exists: {output_path}"
        )
    settings_path = _resolve_init_settings_output(args)
    if settings_path is not None and settings_path.exists() and not args.force:
        raise UpdateError(
            UpdateErrorCode.SETTINGS_INVALID,
            f"settings output already exists: {settings_path}",
        )
    for log_line in _check_init_nas_root(args):
        print(log_line)
    channel = str(args.channel or "stable").strip()
    payload = {
        "channel": channel,
        "installer_mode": str(args.installer_mode or "uot_updater").strip(),
        "manifest_sources": [
            {
                "name": str(args.source_name or "local-nas").strip(),
                "manifest_url": f"uot-nas://{app_id}/{channel}",
                "package_url_prefix": str(
                    args.package_url_prefix or "uot-nas://nas"
                ).strip(),
                "auth_provider": str(
                    args.auth_provider or "update_online_tool"
                ).strip(),
                "priority": int(args.priority),
            }
        ],
    }
    _write_json(output_path, payload)
    if settings_path is not None:
        _write_json(settings_path, _build_init_settings_payload(args))
    print(f"Generated {output_path}")
    if settings_path is not None:
        print(f"Generated {settings_path}")
    return 0


def _resolve_init_settings_output(args: argparse.Namespace) -> Path | None:
    """解析 init 命令 settings 输出路径。

    :param args: 命令参数。
    :return: settings 输出路径；未请求生成时返回 None。
    """
    if args.settings_output is not None:
        return Path(args.settings_output)
    if args.nas_root is not None:
        if bool(args.user_settings):
            return user_settings_path(_resolve_init_app_id(args))
        return Path(args.output).parent / "config" / "settings.json"
    return None


def _check_init_nas_root(args: argparse.Namespace) -> list[str]:
    """检查 init 命令传入的 NAS 根目录。

    :param args: 命令参数。
    :return: 检查日志。
    """
    if args.nas_root is None:
        return []
    nas_root = normalize_nas_root(args.nas_root)
    if bool(args.skip_nas_check):
        return [f"NAS check skipped: root={nas_root}"]
    return _probe_nas_root(nas_root)


def _probe_nas_root(nas_root: Path) -> list[str]:
    """探测 NAS 根目录是否可读写。

    :param nas_root: NAS 根目录。
    :return: 检查日志。
    """
    root = Path(nas_root)
    if not root.is_dir():
        raise UpdateError(
            UpdateErrorCode.SETTINGS_INVALID, f"NAS root is not available: {root}"
        )
    try:
        next(root.iterdir(), None)
    except OSError as exc:
        raise UpdateError(
            UpdateErrorCode.SETTINGS_INVALID, f"NAS root is not readable: {root}"
        ) from exc
    probe_path = root / f".uot-write-test-{os.getpid()}-{uuid4().hex}.tmp"
    probe_text = "update-online-tool nas check\n"
    try:
        probe_path.write_text(probe_text, encoding="utf-8")
        if probe_path.read_text(encoding="utf-8") != probe_text:
            raise UpdateError(
                UpdateErrorCode.SETTINGS_INVALID,
                f"NAS root write probe mismatch: {root}",
            )
        probe_path.unlink()
    except UpdateError:
        raise
    except OSError as exc:
        _cleanup_probe_file(probe_path)
        raise UpdateError(
            UpdateErrorCode.SETTINGS_INVALID, f"NAS root is not writable: {root}"
        ) from exc
    return [
        f"NAS check ok: root={root}",
        "NAS check ok: readable",
        "NAS check ok: writable",
    ]


def _cleanup_probe_file(path: Path) -> None:
    """清理 NAS 探测临时文件。

    :param path: 临时文件路径。
    :return: None
    """
    try:
        if path.exists():
            path.unlink()
    except OSError:
        return


def _resolve_init_app_id(args: argparse.Namespace) -> str:
    """解析 init 命令应用标识。

    :param args: 命令参数。
    :return: 应用标识。
    """
    app_id = str(args.app or "").strip()
    if not app_id:
        app_id = Path.cwd().name.strip()
    if not app_id:
        raise UpdateError(
            UpdateErrorCode.SETTINGS_INVALID,
            "app id is required outside a named project directory",
        )
    return app_id


def _build_init_settings_payload(args: argparse.Namespace) -> dict[str, object]:
    """构建 init 命令 settings payload。

    :param args: 命令参数。
    :return: settings JSON 字典。
    """
    if args.nas_root is None:
        raise UpdateError(
            UpdateErrorCode.SETTINGS_INVALID,
            "--nas-root is required when writing settings",
        )
    return {
        "nas": {
            "root": str(normalize_nas_root(args.nas_root)),
        },
        "publish": {
            "default_channel": str(args.channel or "stable").strip(),
            "default_minimum_version": "1.0.0",
            "package_filename": "package.zip",
        },
        "updater": {
            "executable_name": str(args.updater_name or "Updater.exe").strip(),
        },
    }


def _publish(args: argparse.Namespace) -> int:
    """发布包到 NAS。

    :param args: 命令参数。
    :return: 进程退出码。
    """
    settings = _load_settings_arg(args)
    source = NasReleaseSource(settings.nas_root)
    app_id = validate_release_component(args.app, "app_id")
    channel = validate_release_component(
        args.channel or settings.default_channel, "channel"
    )
    version = normalize_release_version(args.version)
    package_filename = validate_release_component(
        settings.package_filename,
        "publish.package_filename",
        maximum_length=255,
    )
    platform = _normalize_optional_platform(args.platform)
    min_supported_version = (
        args.min_supported_version or settings.default_minimum_version
    )
    source_package_path = Path(args.package)
    if not source_package_path.is_file():
        raise UpdateError(
            UpdateErrorCode.PACKAGE_NOT_FOUND,
            f"package not found: {source_package_path}",
        )
    ReleasePackagePlan.from_zip(source_package_path, platform=platform)
    if args.release_contract is not None:
        _validate_publish_release_contract(
            Path(args.release_contract),
            app_id=app_id,
            version=version,
            platform=platform,
            package_path=source_package_path,
        )
    notes = _resolve_publish_notes(args)
    target_package_path = source.package_path(
        app_id, version, package_filename, platform, channel
    )
    version_manifest_path = (
        source.version_dir(app_id, version, platform, channel) / "latest.json"
    )
    channel_manifest_path = source.manifest_path(app_id, channel, platform)
    versions_index_path = source.versions_index_path(app_id, channel, platform)
    with _publish_lock(
        source=source, app_id=app_id, channel=channel, platform=platform
    ):
        with _publish_file_transaction(
            (
                target_package_path,
                version_manifest_path,
                channel_manifest_path,
                versions_index_path,
            )
        ):
            _copy_file_atomic(
                source_package_path,
                target_package_path,
                validator=lambda path: ReleasePackagePlan.from_zip(
                    path,
                    platform=platform,
                ),
            )
            package_size = target_package_path.stat().st_size
            package_sha256 = _sha256_of(target_package_path)
            relative_package_url = _package_url(
                app_id, version, package_filename, platform, channel
            )
            published_at = (
                args.published_at.strip() or datetime.now(timezone.utc).isoformat()
            )
            payload: dict[str, object] = {
                "schema_version": 2,
                "app_id": app_id,
                "channel": channel,
                "version": version,
                "mandatory": bool(args.mandatory),
                "min_supported_version": min_supported_version,
                "published_at": published_at,
                "notes": notes,
                "package": {
                    "url": relative_package_url,
                    "size": package_size,
                    "sha256": package_sha256,
                },
            }
            if platform:
                payload["platform"] = platform
            payload.update(_manifest_policy_payload(args))
            manifest = UpdateManifest.from_payload(payload)
            manifest_payload = manifest.to_payload()
            if args.sign_key is not None:
                manifest_payload = sign_manifest_payload_with_key_file(
                    manifest_payload,
                    key_path=Path(args.sign_key),
                    key_id=args.key_id,
                )
            manifest = UpdateManifest.from_payload(manifest_payload)
            _write_json(version_manifest_path, manifest_payload)
            _write_json(channel_manifest_path, manifest_payload)
            _update_versions_index(
                source=source,
                app_id=app_id,
                channel=channel,
                platform=platform,
                manifest=manifest,
            )
    print(f"Published {app_id} v{version} to {settings.nas_root}")
    return 0


def _verify(args: argparse.Namespace) -> int:
    """校验 NAS 发布内容。

    :param args: 命令参数。
    :return: 进程退出码。
    """
    settings = _load_settings_arg(args)
    source = NasReleaseSource(settings.selected_nas_root())
    source.ensure_available()
    channel = args.channel or settings.default_channel
    platform = _normalize_optional_platform(args.platform)
    manifest_path = source.manifest_path(args.app, channel, platform)
    manifest_payload = _load_manifest_payload(manifest_path)
    if args.signature_key is not None:
        verify_manifest_signature_with_key_file(
            manifest_payload, key_path=Path(args.signature_key)
        )
    manifest = UpdateManifest.from_payload(manifest_payload)
    if platform and manifest.platform and manifest.platform != platform:
        raise UpdateError(
            UpdateErrorCode.MANIFEST_INVALID,
            f"manifest platform mismatch: {manifest.platform}",
        )
    package_path = source.resolve_package_path(manifest.package.url)
    if not package_path.is_file():
        raise UpdateError(
            UpdateErrorCode.PACKAGE_NOT_FOUND, f"package not found: {package_path}"
        )
    actual_size = package_path.stat().st_size
    if actual_size != manifest.package.size:
        raise UpdateError(
            UpdateErrorCode.PACKAGE_SIZE_MISMATCH,
            f"package.size {manifest.package.size} != actual {actual_size}",
        )
    actual_sha256 = _sha256_of(package_path)
    if actual_sha256 != manifest.package.sha256.lower():
        raise UpdateError(
            UpdateErrorCode.PACKAGE_HASH_MISMATCH,
            f"package.sha256 {manifest.package.sha256.lower()} != actual {actual_sha256}",
        )
    ReleasePackagePlan.from_zip(package_path, platform=manifest.platform or platform)
    print(f"Verified {manifest_path}")
    return 0


def _check(args: argparse.Namespace) -> int:
    """检查是否存在升级。

    :param args: 命令参数。
    :return: 进程退出码。
    """
    settings = _load_settings_arg(args)
    channel = args.channel or settings.default_channel
    platform = _normalize_optional_platform(args.platform)
    result = UpdateService(settings).check(
        app_id=args.app,
        current_version=args.current_version,
        channel=channel,
        platform=platform,
        skipped_version=args.skipped_version or None,
    )
    _verify_manifest_payload_if_requested(
        result.manifest.to_payload(), args.signature_key
    )
    print(f"{result.decision.value}: {result.manifest.version}")
    return 0


def _list_remote(args: argparse.Namespace) -> int:
    """列出 NAS 历史版本。"""
    settings = _load_settings_arg(args)
    channel = args.channel or settings.default_channel
    platform = _normalize_optional_platform(args.platform)
    versions = UpdateService(settings).list_remote_versions(
        app_id=args.app,
        channel=channel,
        platform=platform,
        include_hidden=bool(args.include_hidden),
    )
    if args.signature_key is not None:
        for item in versions:
            _verify_manifest_payload_if_requested(
                item.manifest.to_payload(), args.signature_key
            )
    payload = {
        "app_id": args.app,
        "channel": channel,
        "platform": platform,
        "versions": [
            {
                "version": item.version,
                "channel": item.channel,
                "platform": item.platform,
                "manifest_path": str(item.manifest_path),
                "package_url": item.manifest.package.url,
                "package_size": item.manifest.package.size,
                "package_exists": item.package_exists,
                "published_at": item.manifest.published_at,
                "mandatory": item.manifest.mandatory,
                "allow_downgrade": item.manifest.allow_downgrade,
                "hidden": item.manifest.hidden,
                "requires_confirmation": item.manifest.requires_confirmation,
                "rollout_percent": item.manifest.rollout_percent,
                "data_schema_version": item.manifest.data_schema_version,
                "signature_algorithm": item.manifest.signature.algorithm
                if item.manifest.signature
                else "",
                "signature_key_id": item.manifest.signature.key_id
                if item.manifest.signature
                else "",
                "notes": item.notes,
            }
            for item in versions
        ],
    }
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


def _manifest_policy_payload(args: argparse.Namespace) -> dict[str, object]:
    """从 publish 参数生成 manifest 策略字段。"""
    payload: dict[str, object] = {}
    if bool(args.allow_downgrade):
        payload["allow_downgrade"] = True
    if bool(args.hidden):
        payload["hidden"] = True
    if bool(args.requires_confirmation):
        payload["requires_confirmation"] = True
    rollout_percent = int(args.rollout_percent)
    if rollout_percent != 100:
        payload["rollout_percent"] = rollout_percent
    data_schema_version = int(args.data_schema_version)
    if data_schema_version:
        payload["data_schema_version"] = data_schema_version
    return payload


def _resolve_publish_notes(args: argparse.Namespace) -> str:
    """解析 publish 使用的版本说明。

    :param args: 命令参数。
    :return: 版本说明文本。
    """
    notes_file = getattr(args, "notes_file", None)
    if notes_file is not None:
        path = Path(notes_file)
        if not path.is_file():
            raise UpdateError(
                UpdateErrorCode.SETTINGS_INVALID, f"notes file not found: {path}"
            )
        notes = path.read_text(encoding="utf-8").strip()
        if not notes:
            raise UpdateError(
                UpdateErrorCode.SETTINGS_INVALID, f"notes file is empty: {path}"
            )
        return notes
    notes = str(getattr(args, "notes", "") or "").strip()
    if notes:
        return notes
    return f"v{args.version} release"


def _show_version(args: argparse.Namespace) -> int:
    """输出指定版本 manifest。"""
    settings = _load_settings_arg(args)
    platform = _normalize_optional_platform(args.platform)
    manifest = UpdateService(settings).get_remote_manifest(
        app_id=args.app,
        version=args.version,
        channel=args.channel or settings.default_channel,
        platform=platform,
    )
    _verify_manifest_payload_if_requested(manifest.to_payload(), args.signature_key)
    print(json.dumps(manifest.to_payload(), ensure_ascii=False, indent=2))
    return 0


def _prepare_version(args: argparse.Namespace) -> int:
    """准备指定版本升级包。"""
    settings = _load_settings_arg(args)
    platform = _normalize_optional_platform(args.platform)
    service = UpdateService(settings)
    manifest, manifest_path = service.get_remote_manifest_with_path(
        app_id=args.app,
        version=args.version,
        channel=args.channel or settings.default_channel,
        platform=platform,
    )
    _verify_manifest_payload_if_requested(manifest.to_payload(), args.signature_key)
    prepared = service.prepare(manifest, Path(args.download_dir))
    payload = {
        "app_id": manifest.app_id,
        "version": manifest.version,
        "channel": manifest.channel,
        "platform": manifest.platform,
        "manifest_path": str(manifest_path),
        "package_path": str(prepared.package_path),
        "sha256": prepared.sha256,
        "verified": prepared.verified,
    }
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


def _list_installed(args: argparse.Namespace) -> int:
    """列出安装根已安装版本。"""
    install_root = normalize_install_root(Path(args.install_root))
    versions = list_installed_versions(
        install_root=install_root, entry_name=args.entry_name
    )
    payload = {
        "install_root": str(install_root),
        "versions": [
            {
                "version": item.version,
                "release_dir": str(item.release_dir),
                "entry_path": str(item.entry_path),
                "entry_exists": item.entry_exists,
                "entry_kind": item.entry_kind,
                "is_current": item.is_current,
            }
            for item in versions
        ],
    }
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


def _switch_installed(args: argparse.Namespace) -> int:
    """切换安装根 current.json。"""
    platform = _normalize_optional_platform(args.platform)
    result = switch_installed_release(
        install_root=Path(args.install_root),
        version=args.version,
        entry_name=args.entry_name,
        app_id=args.app,
        platform=platform,
        wait_pid=args.wait_pid,
        wait_timeout=float(args.wait_timeout),
        restart=bool(args.restart),
    )
    print(json.dumps(result.to_payload(), ensure_ascii=False, indent=2))
    return 0


def _migrate_install_root(args: argparse.Namespace) -> int:
    """迁移旧版平铺安装根。"""
    result = migrate_install_root(
        install_root=Path(args.install_root),
        version=args.version,
        entry_name=args.entry_name,
        app_id=args.app,
        platform=_normalize_optional_platform(args.platform),
        force=bool(args.force),
        dry_run=bool(args.dry_run),
    )
    print(json.dumps(result.to_payload(), ensure_ascii=False, indent=2))
    return 0


def _write_migration_package(args: argparse.Namespace) -> int:
    """生成旧客户端迁移包模板。"""
    result = write_migration_package_template(
        output_dir=Path(args.output_dir),
        app_id=args.app,
        version=args.version,
        entry_name=args.entry_name,
        platform=_normalize_optional_platform(args.platform),
        updater_bundle=args.updater_bundle,
        settings_path=args.settings,
        endpoint_path=args.endpoint,
        force=bool(args.force),
    )
    print(json.dumps(result.to_payload(), ensure_ascii=False, indent=2))
    return 0


def _verify_migration_package(args: argparse.Namespace) -> int:
    """校验旧客户端迁移包模板。"""
    result = verify_migration_package(package_dir=Path(args.package_dir))
    print(json.dumps(result.to_payload(), ensure_ascii=False, indent=2))
    return 0 if result.valid else 1


def _install_prepared(args: argparse.Namespace) -> int:
    """安装已准备的升级包。"""
    manifest_payload = _load_manifest_payload(Path(args.manifest))
    if args.signature_key is not None:
        verify_manifest_signature_with_key_file(
            manifest_payload, key_path=Path(args.signature_key)
        )
    manifest = UpdateManifest.from_payload(manifest_payload)
    result = install_prepared_package(
        install_root=Path(args.install_root),
        package_path=Path(args.package),
        manifest=manifest,
        entry_name=args.entry_name,
        switch_current=not bool(args.no_switch),
        force=bool(args.force),
        dry_run=bool(args.dry_run),
        wait_pid=args.wait_pid,
        wait_timeout=float(args.wait_timeout),
        restart=bool(args.restart),
    )
    print(json.dumps(result.to_payload(), ensure_ascii=False, indent=2))
    return 0


def _apply_update(args: argparse.Namespace) -> int:
    """应用 pending-update.json。"""
    result = apply_pending_update(
        pending_path=Path(args.pending),
        signature_key=Path(args.signature_key)
        if args.signature_key is not None
        else None,
        entry_name=args.entry_name,
        force=bool(args.force),
        dry_run=bool(args.dry_run),
        wait_pid=args.wait_pid,
        wait_timeout=float(args.wait_timeout),
        restart=bool(args.restart),
    )
    print(json.dumps(result.to_payload(), ensure_ascii=False, indent=2))
    return 0


def _rollback(args: argparse.Namespace) -> int:
    """回滚到 previous_version。"""
    result = rollback_installation(
        install_root=Path(args.install_root),
        entry_name=args.entry_name,
        wait_pid=args.wait_pid,
        wait_timeout=float(args.wait_timeout),
        restart=bool(args.restart),
    )
    print(json.dumps(result.to_payload(), ensure_ascii=False, indent=2))
    return 0


def _launch_current(args: argparse.Namespace) -> int:
    """启动当前 release。"""
    process = launch_current(install_root=Path(args.install_root))
    print(json.dumps({"pid": process.pid}, ensure_ascii=False, indent=2))
    return 0


def _doctor(args: argparse.Namespace) -> int:
    """收集安装根诊断报告。"""
    report = collect_diagnostics(
        install_root=Path(args.install_root), entry_name=args.entry_name
    )
    if args.archive is not None:
        archive_path = write_diagnostic_archive(
            report=report,
            install_root=Path(args.install_root),
            archive_path=Path(args.archive),
        )
        report["archive"] = str(archive_path)
    report_text = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
    if args.output is not None:
        Path(args.output).parent.mkdir(parents=True, exist_ok=True)
        Path(args.output).write_text(report_text, encoding="utf-8")
    print(report_text, end="")
    return 0


def _write_updater_spec(args: argparse.Namespace) -> int:
    """生成 uot-updater PyInstaller spec。"""
    result = write_updater_pyinstaller_spec(
        output_dir=Path(args.output_dir),
        name=args.name,
        onefile=bool(args.onefile),
        console=not bool(args.windowed),
        force=bool(args.force),
    )
    print(json.dumps(result.to_payload(), ensure_ascii=False, indent=2))
    return 0


def _write_agent_spec(args: argparse.Namespace) -> int:
    """生成 uot-agent PyInstaller spec。"""
    result = write_agent_pyinstaller_spec(
        output_dir=Path(args.output_dir),
        name=args.name,
        onefile=bool(args.onefile),
        console=not bool(args.windowed),
        force=bool(args.force),
    )
    print(json.dumps(result.to_payload(), ensure_ascii=False, indent=2))
    return 0


def _write_bootstrap_spec(args: argparse.Namespace) -> int:
    """生成 uot-bootstrap PyInstaller spec。"""
    result = write_bootstrap_pyinstaller_spec(
        output_dir=Path(args.output_dir),
        name=args.name,
        onefile=bool(args.onefile),
        console=not bool(args.windowed),
        force=bool(args.force),
    )
    print(json.dumps(result.to_payload(), ensure_ascii=False, indent=2))
    return 0


def _write_bridge_spec(args: argparse.Namespace) -> int:
    """生成 uot-bridge PyInstaller spec。"""
    result = write_bridge_pyinstaller_spec(
        output_dir=Path(args.output_dir),
        name=args.name,
        onefile=bool(args.onefile),
        console=not bool(args.windowed),
        force=bool(args.force),
    )
    print(json.dumps(result.to_payload(), ensure_ascii=False, indent=2))
    return 0


def _assemble_pyinstaller(args: argparse.Namespace) -> int:
    """装配 PyInstaller 发布目录。

    :param args: 命令参数。
    :return: 进程退出码。
    """
    config = default_pyinstaller_assembly_config(
        version=args.version,
        app_id=args.app,
        dist_dir=Path(args.dist_dir),
        product_name=args.product_name,
        platform=args.platform,
        entry_name=args.entry_name,
        release_entry_name=args.release_entry_name,
        launcher_entry_name=args.launcher_entry_name,
        settings_path=args.settings,
        updater_bundle=args.updater_bundle,
        updater_name=args.updater_name,
        bootstrap_agent_mode=bool(args.bootstrap_agent_mode),
        agent_bundle=args.agent_bundle,
        agent_name=args.agent_name,
        force=bool(args.force),
    )
    result = assemble_pyinstaller_release(config)
    print(f"Assembled install root: {result.install_root}")
    print(f"Assembled update root: {result.update_root}")
    print(f"Launcher executable: {result.launcher_executable}")
    print(f"Release executable: {result.release_executable}")
    if result.updater_path is not None:
        print(f"Updater bundle: {result.updater_path}")
    if result.agent_path is not None:
        print(f"Agent bundle: {result.agent_path}")
    return 0


def _assemble_release(args: argparse.Namespace) -> int:
    """装配 Tauri、Electron 等框架无关的稳定 UOT release。"""
    result = assemble_release_layout(
        ReleaseAssemblyConfig(
            version=args.version,
            app_id=args.app,
            release_dir=Path(args.release_dir),
            launcher_dir=Path(args.bootstrap_dir),
            install_output=Path(args.install_output),
            update_output=Path(args.update_output),
            platform=args.platform,
            entry_path=args.entry_path,
            release_entry_path=args.release_entry,
            launcher_entry_path=args.bootstrap_entry,
            bootstrap_agent_mode=True,
            agent_bundle=Path(args.agent_bundle),
            agent_name=args.agent_name,
            force=bool(args.force),
        )
    )
    print(f"Assembled install root: {result.install_root}")
    print(f"Assembled update root: {result.update_root}")
    print(f"Bootstrap executable: {result.launcher_entry}")
    print(f"Release executable: {result.release_entry}")
    print(f"Agent bundle: {result.agent_path}")
    return 0


def _package_release(args: argparse.Namespace) -> int:
    """创建 Unicode 安全的 release 包。"""
    package = create_release_package(
        Path(args.source_dir),
        Path(args.output),
        force=bool(args.force),
    )
    print(f"Created release package: {package}")
    return 0


def _add_release_contract_args(parser: argparse.ArgumentParser) -> None:
    """添加 release 完整性契约的公共参数。"""
    parser.add_argument(
        "--release-dir", required=True, type=Path, help="Release root directory."
    )
    parser.add_argument("--app", required=True, help="Application id.")
    parser.add_argument("--version", required=True, help="Release version.")
    parser.add_argument(
        "--platform",
        required=True,
        choices=["windows", "macos", "linux"],
        help="Target platform.",
    )
    parser.add_argument(
        "--entry-path", required=True, help="Entry path relative to release root."
    )
    parser.add_argument(
        "--required-path",
        dest="required_paths",
        action="append",
        default=[],
        help="Required file path relative to release root; repeat for multiple files.",
    )


def _write_release_contract(args: argparse.Namespace) -> int:
    """写入框架产物的 UOT release 契约。"""
    target = Path(args.release_dir) / "uot-release.json"
    if target.exists() and not bool(args.force):
        raise UpdateError(
            UpdateErrorCode.SETTINGS_INVALID,
            f"release contract already exists: {target}",
        )
    contract = ReleaseArtifactContract(
        app_id=str(args.app).strip(),
        version=str(args.version).strip().removeprefix("v").removeprefix("V"),
        platform=str(args.platform).strip(),
        entry_path=str(args.entry_path).strip(),
        required_paths=tuple(str(item) for item in args.required_paths),
    )
    path = write_release_contract(Path(args.release_dir), contract)
    print(f"Wrote release contract: {path}")
    return 0


def _validate_release(args: argparse.Namespace) -> int:
    """执行发布前或运行时 release 完整性校验。"""
    contract = validate_release_artifact(
        release_dir=Path(args.release_dir),
        version=str(args.version),
        entry_path=str(args.entry_path),
        app_id=str(args.app),
        platform=str(args.platform),
        required_paths=tuple(str(item) for item in args.required_paths),
    )
    print(
        json.dumps(
            {
                "ok": True,
                "release_dir": str(Path(args.release_dir)),
                "contract": contract.to_payload() if contract is not None else None,
            },
            ensure_ascii=False,
        )
    )
    return 0


def _validate_publish_release_contract(
    path: Path,
    *,
    app_id: str,
    version: str,
    platform: str,
    package_path: Path,
) -> None:
    """确认 NAS manifest 身份与已验证 release 契约一致。"""
    payload = _load_manifest_payload(Path(path))
    try:
        contract = ReleaseArtifactContract.from_payload(payload)
    except UpdateError as exc:
        raise UpdateError(
            UpdateErrorCode.MANIFEST_INVALID,
            f"invalid release contract: {path}: {exc.message}",
        ) from exc
    normalized_version = str(version).strip().removeprefix("v").removeprefix("V")
    if contract.app_id != str(app_id).strip():
        raise UpdateError(
            UpdateErrorCode.SETTINGS_INVALID,
            "release contract app_id does not match publish --app",
        )
    if contract.version != normalized_version:
        raise UpdateError(
            UpdateErrorCode.SETTINGS_INVALID,
            "release contract version does not match publish --version",
        )
    if platform and contract.platform != platform:
        raise UpdateError(
            UpdateErrorCode.SETTINGS_INVALID,
            "release contract platform does not match publish --platform",
        )
    try:
        with zipfile.ZipFile(package_path) as archive:
            packaged_payload = json.loads(
                archive.read(RELEASE_CONTRACT_FILENAME).decode("utf-8")
            )
    except KeyError as exc:
        raise UpdateError(
            UpdateErrorCode.MANIFEST_INVALID,
            "release package does not contain uot-release.json",
        ) from exc
    except (
        OSError,
        zipfile.BadZipFile,
        UnicodeDecodeError,
        json.JSONDecodeError,
    ) as exc:
        raise UpdateError(
            UpdateErrorCode.MANIFEST_INVALID,
            f"cannot read release contract from package: {package_path}",
        ) from exc
    if not isinstance(packaged_payload, dict):
        raise UpdateError(
            UpdateErrorCode.MANIFEST_INVALID,
            "release package contract must be a JSON object",
        )
    packaged_contract = ReleaseArtifactContract.from_payload(packaged_payload)
    if packaged_contract != contract:
        raise UpdateError(
            UpdateErrorCode.MANIFEST_INVALID,
            "release package contract does not match --release-contract",
        )


def _load_settings_arg(args: argparse.Namespace) -> UpdateToolSettings:
    """按命令参数读取设置。

    :param args: 命令参数。
    :return: 设置模型。
    """
    return UpdateToolSettings.load(args.settings or None)


def _load_manifest_file(path: Path) -> UpdateManifest:
    """读取 manifest 文件。

    :param path: manifest 路径。
    :return: manifest 模型。
    """
    return UpdateManifest.from_payload(_load_manifest_payload(path))


def _load_manifest_payload(path: Path) -> dict[str, object]:
    """读取 manifest JSON 字典。"""
    if not path.is_file():
        raise UpdateError(
            UpdateErrorCode.MANIFEST_NOT_FOUND, f"manifest not found: {path}"
        )
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise UpdateError(
            UpdateErrorCode.MANIFEST_INVALID, "manifest must be a JSON object"
        )
    return payload


def _write_json(path: Path, payload: dict[str, object]) -> None:
    """写入稳定格式 JSON。

    :param path: 目标路径。
    :param payload: JSON 字典。
    :return: None
    """
    _write_text_atomic(path, json.dumps(payload, ensure_ascii=False, indent=2) + "\n")


def _write_text_atomic(path: Path, content: str) -> None:
    """原子写入文本文件。

    :param path: 目标路径。
    :param content: 文件内容。
    :return: None
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.parent / f".{path.name}.{os.getpid()}.{uuid4().hex}.tmp"
    try:
        temp_path.write_text(content, encoding="utf-8")
        temp_path.replace(path)
    except Exception:
        if temp_path.exists():
            temp_path.unlink()
        raise


def _copy_file_atomic(
    source_path: Path,
    target_path: Path,
    *,
    validator: Callable[[Path], object] | None = None,
) -> None:
    """复制文件到同目录临时文件后原子替换目标。

    :param source_path: 源文件。
    :param target_path: 目标文件。
    :param validator: 原子提升前针对临时副本执行的校验器。
    :return: None
    """
    target_path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = (
        target_path.parent / f".{target_path.name}.{os.getpid()}.{uuid4().hex}.tmp"
    )
    try:
        shutil.copy2(source_path, temp_path)
        if validator is not None:
            validator(temp_path)
        temp_path.replace(target_path)
    except Exception:
        if temp_path.exists():
            temp_path.unlink()
        raise


@contextmanager
def _publish_lock(
    *, source: NasReleaseSource, app_id: str, channel: str, platform: str
) -> Iterator[None]:
    """发布锁，防止同一 app/channel/platform 并发写 latest 和 versions index。

    :param source: NAS 发布源。
    :param app_id: 应用标识。
    :param channel: 发布通道。
    :param platform: 平台。
    :return: None。
    """
    lock_root = source.versions_index_path(app_id, channel, platform).parent
    lock_root.mkdir(parents=True, exist_ok=True)
    lock_path = lock_root / "publish.lock"
    try:
        fd = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError as exc:
        raise UpdateError(
            UpdateErrorCode.UPDATE_LOCKED, f"publish lock exists: {lock_path}"
        ) from exc
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as lock_file:
            lock_file.write(
                json.dumps(
                    {
                        "pid": os.getpid(),
                        "app_id": app_id,
                        "channel": channel,
                        "platform": platform,
                    },
                    ensure_ascii=False,
                )
                + "\n"
            )
        yield
    finally:
        try:
            lock_path.unlink()
        except FileNotFoundError:
            return


@contextmanager
def _publish_file_transaction(paths: tuple[Path, ...]) -> Iterator[None]:
    """在单次发布异常时恢复 package、manifest 与版本索引。"""
    snapshots: list[tuple[Path, Path | None]] = []
    try:
        for path in dict.fromkeys(Path(item) for item in paths):
            backup: Path | None = None
            if path.is_file():
                backup = path.with_name(
                    f".{path.name}.{os.getpid()}.{uuid4().hex}.backup"
                )
                try:
                    shutil.copy2(path, backup)
                except Exception:
                    if backup.exists():
                        backup.unlink()
                    raise
            snapshots.append((path, backup))
        yield
    except Exception as exc:
        restore_errors: list[str] = []
        for path, backup in reversed(snapshots):
            try:
                if backup is None:
                    if path.exists():
                        path.unlink()
                else:
                    backup.replace(path)
            except OSError as restore_exc:
                restore_errors.append(f"{path}: {restore_exc}")
        if restore_errors:
            raise UpdateError(
                UpdateErrorCode.SETTINGS_INVALID,
                "publish rollback incomplete; preserve .backup files for recovery: "
                + "; ".join(restore_errors),
            ) from exc
        if isinstance(exc, UpdateError):
            raise
        raise UpdateError(
            UpdateErrorCode.SETTINGS_INVALID,
            f"publish transaction failed: {exc}",
        ) from exc
    else:
        for _, backup in snapshots:
            if backup is not None and backup.exists():
                backup.unlink()


def _verify_manifest_payload_if_requested(
    payload: dict[str, object], signature_key: Path | None
) -> None:
    """按需校验 manifest payload 签名。"""
    if signature_key is not None:
        verify_manifest_signature_with_key_file(payload, key_path=Path(signature_key))


def _update_versions_index(
    *,
    source: NasReleaseSource,
    app_id: str,
    channel: str,
    platform: str,
    manifest: UpdateManifest,
) -> None:
    """更新通道 versions.json 索引。"""
    index_path = source.versions_index_path(app_id, channel, platform)
    existing_versions: list[dict[str, object]] = []
    if index_path.is_file():
        payload = json.loads(index_path.read_text(encoding="utf-8"))
        if isinstance(payload, dict) and isinstance(payload.get("versions"), list):
            existing_versions = [
                item for item in payload["versions"] if isinstance(item, dict)
            ]
    entry = {
        "version": manifest.version,
        "manifest_url": _manifest_url(app_id, manifest.version, platform, channel),
        "package_url": manifest.package.url,
        "published_at": manifest.published_at,
        "mandatory": manifest.mandatory,
        "allow_downgrade": manifest.allow_downgrade,
        "hidden": manifest.hidden,
        "requires_confirmation": manifest.requires_confirmation,
        "rollout_percent": manifest.rollout_percent,
        "data_schema_version": manifest.data_schema_version,
        "notes": manifest.notes,
    }
    versions = [
        item for item in existing_versions if item.get("version") != manifest.version
    ]
    versions.append(entry)
    versions = sorted(
        versions,
        key=lambda item: parse_version_tuple(str(item.get("version", ""))),
        reverse=True,
    )
    _write_json(
        index_path,
        {
            "schema_version": 1,
            "app_id": app_id,
            "channel": channel,
            "platform": platform,
            "versions": versions,
        },
    )


def _package_url(
    app_id: str, version: str, package_filename: str, platform: str, channel: str
) -> str:
    """生成 manifest package.url。

    :param app_id: 应用标识。
    :param version: 版本号。
    :param package_filename: 包文件名。
    :param platform: 可选平台。
    :param channel: 发布通道。
    :return: NAS 根目录下的相对包路径。
    """
    if platform:
        return f"{app_id}/{channel}/v{version}/{platform}/{package_filename}"
    return f"{app_id}/{channel}/v{version}/{package_filename}"


def _manifest_url(app_id: str, version: str, platform: str, channel: str) -> str:
    """生成版本 manifest 相对路径。

    :param app_id: 应用标识。
    :param version: 版本号。
    :param platform: 可选平台。
    :param channel: 发布通道。
    :return: NAS 根目录下的版本 manifest 相对路径。
    """
    if platform:
        return f"{app_id}/{channel}/v{version}/{platform}/latest.json"
    return f"{app_id}/{channel}/v{version}/latest.json"


def _normalize_optional_platform(platform: str) -> str:
    """规范化可选平台参数。

    :param platform: 原始平台名。
    :return: windows、macos、linux 或空字符串。
    """
    return validate_release_platform(
        platform,
        allow_empty=True,
        allow_aliases=True,
    )


def _sha256_of(path: Path) -> str:
    """计算文件 SHA-256。

    :param path: 文件路径。
    :return: 十六进制摘要。
    """
    digest = hashlib.sha256()
    with path.open("rb") as file_obj:
        for chunk in iter(lambda: file_obj.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


if __name__ == "__main__":
    raise SystemExit(main())
