//! UOT 稳定原生 Bootstrap。
//!
//! 此二进制只读取 `current.json` 并启动 active release；它不读取 NAS、不执行
//! 安装事务，也不修改运行时状态。因此可替换 Python 参考 Bootstrap，而不改变
//! Agent request、Bridge 配置或 UOT Core 的更新契约。

use serde::Deserialize;
use serde_json::json;
use std::env;
use std::fs;
use std::path::{Component, Path, PathBuf};
use std::process::{self, Command};
use unicode_normalization::UnicodeNormalization;

const MANIFEST_INVALID: &str = "MANIFEST_INVALID";
const MANIFEST_NOT_FOUND: &str = "MANIFEST_NOT_FOUND";
const SETTINGS_INVALID: &str = "SETTINGS_INVALID";
const UPDATER_LAUNCH_FAILED: &str = "UPDATER_LAUNCH_FAILED";
const UPDATER_NOT_FOUND: &str = "UPDATER_NOT_FOUND";

#[derive(Debug)]
struct UotError {
    code: &'static str,
    message: String,
}

impl UotError {
    fn new(code: &'static str, message: impl Into<String>) -> Self {
        Self {
            code,
            message: message.into(),
        }
    }
}

#[derive(Debug, Default, Deserialize)]
struct CurrentRelease {
    #[serde(default)]
    release_dir: String,
    #[serde(default)]
    executable: String,
    #[serde(default)]
    entry: Option<CurrentEntry>,
}

#[derive(Debug, Default, Deserialize)]
struct CurrentEntry {
    #[serde(default)]
    path: String,
}

fn main() {
    match run(env::args().skip(1).collect()) {
        Ok(pid) => {
            println!("{}", json!({"ok": true, "release_pid": pid}));
        }
        Err(error) => {
            eprintln!(
                "{}",
                json!({"ok": false, "error": {"code": error.code, "message": error.message}})
            );
            process::exit(1);
        }
    }
}

fn run(args: Vec<String>) -> Result<u32, UotError> {
    let install_root = parse_launch_args(args)?;
    launch_current(&install_root)
}

fn parse_launch_args(args: Vec<String>) -> Result<PathBuf, UotError> {
    let [command, option, install_root] = args.as_slice() else {
        return Err(UotError::new(
            SETTINGS_INVALID,
            "usage: uot-bootstrap launch --install-root <path>",
        ));
    };
    if command != "launch" || option != "--install-root" || install_root.trim().is_empty() {
        return Err(UotError::new(
            SETTINGS_INVALID,
            "usage: uot-bootstrap launch --install-root <path>",
        ));
    }
    Ok(PathBuf::from(install_root))
}

fn launch_current(install_root: &Path) -> Result<u32, UotError> {
    let current = read_current_release(install_root)?;
    let root = fs::canonicalize(install_root).map_err(|error| {
        UotError::new(
            SETTINGS_INVALID,
            format!(
                "install root cannot be resolved: {} ({error})",
                install_root.display()
            ),
        )
    })?;
    let release_dir = checked_release_dir(&current.release_dir)?;
    let releases_root = resolve_path_within(
        &root,
        root.join("releases"),
        "releases directory",
        UPDATER_NOT_FOUND,
    )?;
    if !releases_root.is_dir() {
        return Err(UotError::new(
            UPDATER_NOT_FOUND,
            format!("releases directory not found: {}", releases_root.display()),
        ));
    }
    let release_path = resolve_path_within(
        &releases_root,
        root.join(release_dir),
        "current.json release_dir",
        UPDATER_NOT_FOUND,
    )?;
    if !release_path.is_dir() {
        return Err(UotError::new(
            UPDATER_NOT_FOUND,
            format!(
                "current release directory not found: {}",
                release_path.display()
            ),
        ));
    }
    let executable = current.executable.trim();
    let entry_name = if executable.is_empty() {
        current
            .entry
            .as_ref()
            .map(|entry| entry.path.trim())
            .filter(|path| !path.is_empty())
            .ok_or_else(|| {
                UotError::new(
                    SETTINGS_INVALID,
                    "current.json must contain release_dir and executable",
                )
            })?
    } else {
        executable
    };
    let entry_path = resolve_path_within(
        &release_path,
        release_path.join(checked_relative(entry_name, "current.json executable")?),
        "current.json executable",
        UPDATER_NOT_FOUND,
    )?;
    if !is_launchable_entry(&entry_path) {
        return Err(UotError::new(
            UPDATER_NOT_FOUND,
            format!("current entry not found: {}", entry_path.display()),
        ));
    }
    let working_dir = entry_path.parent().ok_or_else(|| {
        UotError::new(
            SETTINGS_INVALID,
            format!(
                "current entry has no parent directory: {}",
                entry_path.display()
            ),
        )
    })?;
    let child = if cfg!(target_os = "macos") && is_macos_app_bundle(&entry_path) {
        Command::new("/usr/bin/open")
            .arg("-n")
            .arg(&entry_path)
            .current_dir(working_dir)
            .spawn()
    } else {
        Command::new(&entry_path).current_dir(working_dir).spawn()
    }
    .map_err(|error| {
        UotError::new(
            UPDATER_LAUNCH_FAILED,
            format!("current release launch failed: {error}"),
        )
    })?;
    Ok(child.id())
}

fn read_current_release(install_root: &Path) -> Result<CurrentRelease, UotError> {
    let path = install_root.join("current.json");
    let payload = fs::read_to_string(&path).map_err(|error| {
        UotError::new(
            MANIFEST_NOT_FOUND,
            format!("current.json not found: {} ({error})", path.display()),
        )
    })?;
    serde_json::from_str(&payload).map_err(|error| {
        UotError::new(
            MANIFEST_INVALID,
            format!(
                "current.json is not valid JSON: {} ({error})",
                path.display()
            ),
        )
    })
}

fn checked_relative(value: &str, field_name: &str) -> Result<PathBuf, UotError> {
    if value != value.trim() {
        return Err(UotError::new(
            SETTINGS_INVALID,
            format!("{field_name} must not start or end with whitespace"),
        ));
    }
    let normalized = value.nfc().collect::<String>();
    let path = Path::new(&normalized);
    if normalized.is_empty()
        || normalized.contains('\\')
        || normalized
            .split('/')
            .any(|component| matches!(component, "." | ".."))
        || path.is_absolute()
        || path.components().any(|component| {
            matches!(
                component,
                Component::CurDir
                    | Component::ParentDir
                    | Component::RootDir
                    | Component::Prefix(_)
            )
        })
    {
        return Err(UotError::new(
            SETTINGS_INVALID,
            format!("{field_name} must be a forward-slash relative path"),
        ));
    }
    for component in normalized.split('/') {
        checked_component(component, field_name)?;
    }
    Ok(path.to_path_buf())
}

fn checked_component(value: &str, field_name: &str) -> Result<(), UotError> {
    let invalid_character = value.chars().any(|character| {
        character.is_control() || matches!(character, '<' | '>' | ':' | '"' | '|' | '?' | '*')
    });
    let reserved_stem = value
        .split('.')
        .next()
        .unwrap_or_default()
        .to_ascii_uppercase();
    let reserved_name = matches!(
        reserved_stem.as_str(),
        "CON"
            | "CONIN$"
            | "CONOUT$"
            | "PRN"
            | "AUX"
            | "NUL"
            | "COM1"
            | "COM2"
            | "COM3"
            | "COM4"
            | "COM5"
            | "COM6"
            | "COM7"
            | "COM8"
            | "COM9"
            | "LPT1"
            | "LPT2"
            | "LPT3"
            | "LPT4"
            | "LPT5"
            | "LPT6"
            | "LPT7"
            | "LPT8"
            | "LPT9"
            | "COM¹"
            | "COM²"
            | "COM³"
            | "LPT¹"
            | "LPT²"
            | "LPT³"
    );
    if value.is_empty()
        || value.len() > 255
        || value != value.trim()
        || value.ends_with([' ', '.'])
        || invalid_character
        || reserved_name
    {
        return Err(UotError::new(
            SETTINGS_INVALID,
            format!("{field_name} contains an unsafe cross-platform path component"),
        ));
    }
    Ok(())
}

fn checked_version(value: &str) -> Result<&str, UotError> {
    checked_component(value, "current.json release version")?;
    let mut characters = value.chars();
    let first = characters.next();
    let last = value.chars().next_back();
    let valid = value.len() <= 128
        && first.is_some_and(|character| character.is_ascii_alphanumeric())
        && last.is_some_and(|character| character.is_ascii_alphanumeric())
        && value.chars().all(|character| {
            character.is_ascii_alphanumeric() || matches!(character, '.' | '_' | '+' | '-')
        });
    if !valid {
        return Err(UotError::new(
            SETTINGS_INVALID,
            "current.json release version must use the canonical version grammar",
        ));
    }
    Ok(value)
}

fn checked_release_dir(value: &str) -> Result<PathBuf, UotError> {
    let path = checked_relative(value, "current.json release_dir")?;
    let mut components = path.components();
    let Some(Component::Normal(first)) = components.next() else {
        return Err(UotError::new(
            SETTINGS_INVALID,
            "current.json release_dir must be releases/<version>",
        ));
    };
    let version = components.next();
    if first != "releases"
        || !matches!(version, Some(Component::Normal(_)))
        || components.next().is_some()
    {
        return Err(UotError::new(
            SETTINGS_INVALID,
            "current.json release_dir must be releases/<version>",
        ));
    }
    let Some(Component::Normal(version)) = version else {
        unreachable!("version shape was checked above");
    };
    let Some(version) = version.to_str() else {
        return Err(UotError::new(
            SETTINGS_INVALID,
            "current.json release version must be valid Unicode",
        ));
    };
    checked_version(version)?;
    Ok(path)
}

fn resolve_path_within(
    allowed_root: &Path,
    requested_path: PathBuf,
    field_name: &str,
    missing_code: &'static str,
) -> Result<PathBuf, UotError> {
    let resolved = fs::canonicalize(&requested_path).map_err(|error| {
        UotError::new(
            missing_code,
            format!(
                "{field_name} not found: {} ({error})",
                requested_path.display()
            ),
        )
    })?;
    if !resolved.starts_with(allowed_root) {
        return Err(UotError::new(
            SETTINGS_INVALID,
            format!(
                "{field_name} must resolve within {}",
                allowed_root.display()
            ),
        ));
    }
    Ok(resolved)
}

fn is_launchable_entry(path: &Path) -> bool {
    path.is_file() || (cfg!(target_os = "macos") && is_macos_app_bundle(path))
}

fn is_macos_app_bundle(path: &Path) -> bool {
    path.is_dir()
        && path.extension().is_some_and(|extension| extension == "app")
        && path.join("Contents/MacOS").is_dir()
        && fs::read_dir(path.join("Contents/MacOS"))
            .ok()
            .is_some_and(|entries| entries.flatten().any(|entry| entry.path().is_file()))
}

#[cfg(test)]
mod tests {
    use super::*;
    #[cfg(unix)]
    use std::os::unix::fs::PermissionsExt;
    use std::sync::atomic::{AtomicU64, Ordering};
    use std::time::{SystemTime, UNIX_EPOCH};

    static TEST_ROOT_SEQUENCE: AtomicU64 = AtomicU64::new(0);

    fn temporary_root() -> PathBuf {
        let unique = SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .expect("system time")
            .as_nanos();
        let sequence = TEST_ROOT_SEQUENCE.fetch_add(1, Ordering::Relaxed);
        let root = env::temp_dir().join(format!(
            "uot-bootstrap-{unique}-{}-{sequence}",
            process::id()
        ));
        fs::create_dir_all(&root).expect("create temporary root");
        root
    }

    #[test]
    fn parses_only_the_stable_launch_contract() {
        assert_eq!(
            parse_launch_args(vec![
                "launch".into(),
                "--install-root".into(),
                "/opt/UOT".into()
            ])
            .expect("valid launch arguments"),
            PathBuf::from("/opt/UOT")
        );
        assert!(parse_launch_args(vec!["launch".into()]).is_err());
        assert!(
            parse_launch_args(vec![
                "switch".into(),
                "--install-root".into(),
                "/opt/UOT".into()
            ])
            .is_err()
        );
    }

    #[test]
    fn rejects_paths_outside_the_install_root() {
        assert!(checked_relative("../Product", "entry").is_err());
        assert!(checked_relative(".", "entry").is_err());
        assert!(checked_relative("Product/./run", "entry").is_err());
        assert!(checked_relative("C:\\\\Product.exe", "entry").is_err());
        assert!(checked_relative("/Applications/Product", "entry").is_err());
        assert!(checked_relative("资源/CON.txt", "entry").is_err());
        assert!(checked_relative("资源/CONIN$", "entry").is_err());
        assert!(checked_relative("资源/COM¹", "entry").is_err());
        assert!(checked_relative("dir/ file", "entry").is_err());
        assert!(checked_relative("资源/name. ", "entry").is_err());
        assert!(checked_relative("资源/file:stream", "entry").is_err());
        assert_eq!(
            checked_relative("Product.app", "entry").expect("relative path"),
            PathBuf::from("Product.app")
        );
        assert_eq!(
            checked_relative("资源/应用程序", "entry").expect("Chinese relative path"),
            PathBuf::from("资源/应用程序")
        );
        assert_eq!(
            checked_relative("资源/e\u{301}.txt", "entry").expect("NFD relative path"),
            PathBuf::from("资源/é.txt")
        );
    }

    #[test]
    fn accepts_only_versioned_release_directories() {
        assert!(checked_release_dir("agent").is_err());
        assert!(checked_release_dir("releases").is_err());
        assert!(checked_release_dir("releases/../agent").is_err());
        assert!(checked_release_dir("releases/1.0.0/nested").is_err());
        assert!(checked_release_dir("releases/版本一").is_err());
        assert_eq!(
            checked_release_dir("releases/1.0.0").expect("versioned release directory"),
            PathBuf::from("releases/1.0.0")
        );
    }

    #[test]
    fn reads_executable_or_entry_path_from_current_json() {
        let root = temporary_root();
        let release = root.join("releases/1.0.0");
        fs::create_dir_all(&release).expect("create release");
        fs::write(
            root.join("current.json"),
            r#"{"release_dir":"releases/1.0.0","entry":{"path":"Product"}}"#,
        )
        .expect("write current");

        let current = read_current_release(&root).expect("read current");
        assert_eq!(current.release_dir, "releases/1.0.0");
        assert_eq!(current.entry.expect("entry").path, "Product");
        fs::remove_dir_all(root).expect("remove temporary root");
    }

    #[cfg(unix)]
    #[test]
    fn launches_the_active_release_from_current_json() {
        let root = temporary_root();
        let release = root.join("releases/1.0.0");
        fs::create_dir_all(&release).expect("create release");
        let executable = release.join("Product");
        fs::write(
            &executable,
            "#!/bin/sh\nprintf native-bootstrap > launched.txt\n",
        )
        .expect("write executable");
        let mut permissions = fs::metadata(&executable)
            .expect("read executable metadata")
            .permissions();
        permissions.set_mode(0o755);
        fs::set_permissions(&executable, permissions).expect("mark executable");
        fs::write(
            root.join("current.json"),
            r#"{"release_dir":"releases/1.0.0","executable":"Product"}"#,
        )
        .expect("write current");

        launch_current(&root).expect("launch active release");
        let marker = release.join("launched.txt");
        for _ in 0..100 {
            if marker.is_file() {
                break;
            }
            std::thread::sleep(std::time::Duration::from_millis(10));
        }
        assert_eq!(
            fs::read_to_string(&marker).expect("read launch marker"),
            "native-bootstrap"
        );
        fs::remove_dir_all(root).expect("remove temporary root");
    }

    #[cfg(unix)]
    #[test]
    fn refuses_an_entry_symlink_that_escapes_the_active_release() {
        use std::os::unix::fs::symlink;

        let root = temporary_root();
        let release = root.join("releases/1.0.0");
        fs::create_dir_all(&release).expect("create release");
        let outside = root.join("outside-product");
        fs::write(&outside, "outside").expect("write outside entry");
        symlink(&outside, release.join("Product")).expect("create escaping symlink");
        fs::write(
            root.join("current.json"),
            r#"{"release_dir":"releases/1.0.0","executable":"Product"}"#,
        )
        .expect("write current");

        let error = launch_current(&root).expect_err("reject escaping symlink");
        assert_eq!(error.code, SETTINGS_INVALID);
        fs::remove_dir_all(root).expect("remove temporary root");
    }

    #[cfg(unix)]
    #[test]
    fn refuses_a_release_symlink_that_escapes_the_releases_root() {
        use std::os::unix::fs::symlink;

        let root = temporary_root();
        fs::create_dir_all(root.join("releases")).expect("create releases root");
        let agent_dir = root.join("agent");
        fs::create_dir_all(&agent_dir).expect("create agent directory");
        fs::write(agent_dir.join("Product"), "agent").expect("write agent entry");
        symlink(&agent_dir, root.join("releases/1.0.0")).expect("create escaping release symlink");
        fs::write(
            root.join("current.json"),
            r#"{"release_dir":"releases/1.0.0","executable":"Product"}"#,
        )
        .expect("write current");

        let error = launch_current(&root).expect_err("reject escaping release symlink");
        assert_eq!(error.code, SETTINGS_INVALID);
        fs::remove_dir_all(root).expect("remove temporary root");
    }
}
