# UOT Native Bootstrap

`uot-bootstrap` is the stable, framework-neutral entry point for the
Bootstrap/Agent runtime. It reads only `<install-root>/current.json` and starts
the active release. It does not access NAS, validate packages, modify state, or
perform update transactions; those remain UOT Core and Update Agent duties.

For defense in depth, `release_dir` must use the UOT layout
`releases/<version>`, and both it and the selected entry must resolve inside the
active release. Absolute paths, backslashes, `.`/`..`, and symlink escapes are
rejected.

## Build

```bash
cargo build --release --manifest-path native/uot-bootstrap/Cargo.toml
```

Copy the resulting executable to the stable install root and reference it from
the bridge configuration:

```json
"bootstrap_command": [
  "C:/Apps/MyTool/uot-bootstrap.exe",
  "launch",
  "--install-root",
  "C:/Apps/MyTool"
]
```

The command and JSON output match the Python reference Bootstrap. On macOS, a
valid `.app` entry is launched through `open -n`; Windows and Linux launch the
selected executable directly. Keep the Bootstrap outside `releases/<version>`:
the update package must never replace this binary.

Run its unit tests with:

```bash
cargo test --manifest-path native/uot-bootstrap/Cargo.toml
```
