//! `--smoke-test`: proves that a packaged build works (review:27 §1(d); ADR-0056), then exits.
//!
//! The run prints exactly one line and exits 0 only when both hold:
//! 1. the `main` window's page load reached *Finished*, and the bundle embeds the frontend's `index.html`;
//! 2. the interpreter at the package's own resource path `sidecar/python/…` (never one found on `PATH`),
//!    started with `-I`, reports Python 3.13, a `sys.prefix` inside the package's resource directory, and the
//!    main sidecar's package `app` imported from inside that directory.
//!
//! Success prints `FT_SMOKE_OK {json}`. Anything else, including no verdict within [`TIMEOUT`], prints
//! `FT_SMOKE_FAIL <reason>` and exits 1. It reads nothing outside the package (INV-24).

use std::path::{Path, PathBuf};
use std::process::Command;
use std::sync::{Arc, Mutex};
use std::time::Duration;

use serde_json::Value;
use tauri::webview::PageLoadEvent;
use tauri::{AppHandle, Builder, Manager, Runtime};

pub const FLAG: &str = "--smoke-test";
const TIMEOUT: Duration = Duration::from_secs(120);
const PROBE: &str =
    "import json, sys\nimport app\nprint(json.dumps({'version': list(sys.version_info[:2]), 'prefix': sys.prefix, 'app': app.__file__}))\n";

/// What the bundled interpreter reported, once it passed every check.
#[derive(Debug, PartialEq, Eq)]
pub struct Probe {
    pub prefix: PathBuf,
    pub app: PathBuf,
}

#[derive(Default)]
struct Progress {
    page: bool,
    probe: Option<Probe>,
    done: bool,
}

/// The interpreter shipped in the package, relative to the resource directory (ADR-0056 layout).
fn interpreter(resources: &Path) -> PathBuf {
    let python = resources.join("sidecar").join("python");
    if cfg!(windows) {
        python.join("python.exe")
    } else {
        python.join("bin").join("python3.13")
    }
}

/// Judges the probe's output. `canon` resolves a path on the real file system (`None` if it does not exist);
/// containment is decided on resolved paths, so a link or `..` cannot point outside the package.
pub fn judge(
    exit_ok: bool,
    stdout: &str,
    resources: &Path,
    canon: impl Fn(&Path) -> Option<PathBuf>,
) -> Result<Probe, String> {
    if !exit_ok {
        return Err("the bundled interpreter failed".into());
    }
    let lines: Vec<&str> = stdout.lines().filter(|l| !l.trim().is_empty()).collect();
    let [line] = lines.as_slice() else {
        return Err(format!("expected one probe line, got {}", lines.len()));
    };
    let v: Value =
        serde_json::from_str(line).map_err(|e| format!("probe output is not JSON: {e}"))?;
    if v["version"] != serde_json::json!([3, 13]) {
        return Err(format!(
            "bundled Python is {}, expected [3,13]",
            v["version"]
        ));
    }
    let root = canon(resources).ok_or("resource directory not found")?;
    let inside = |key: &str| -> Result<PathBuf, String> {
        let raw = v[key]
            .as_str()
            .ok_or(format!("probe field {key} missing"))?;
        let path = canon(Path::new(raw)).ok_or(format!("{key} path does not exist"))?;
        if path.starts_with(&root) {
            Ok(path)
        } else {
            Err(format!("{key} is outside the package"))
        }
    };
    Ok(Probe {
        prefix: inside("prefix")?,
        app: inside("app")?,
    })
}

fn run_probe(resources: &Path) -> Result<Probe, String> {
    let mut cmd = Command::new(interpreter(resources));
    cmd.args(["-I", "-B", "-c", PROBE]).current_dir(resources);
    #[cfg(windows)]
    {
        use std::os::windows::process::CommandExt;
        const CREATE_NO_WINDOW: u32 = 0x0800_0000;
        cmd.creation_flags(CREATE_NO_WINDOW);
    }
    let out = cmd
        .output()
        .map_err(|e| format!("cannot start the bundled interpreter: {e}"))?;
    judge(
        out.status.success(),
        &String::from_utf8_lossy(&out.stdout),
        resources,
        |p| std::fs::canonicalize(p).ok(),
    )
}

/// The line to print and the exit code, or `None` while one half is still outstanding. A failure decides at
/// once; success needs both the finished page load and a passing probe.
fn verdict(
    failure: Option<String>,
    page: bool,
    probe: Option<&Probe>,
    version: &str,
) -> Option<(String, i32)> {
    match (failure, probe) {
        (Some(reason), _) => Some((format!("FT_SMOKE_FAIL {reason}"), 1)),
        (None, Some(probe)) if page => Some((
            format!(
                "FT_SMOKE_OK {}",
                serde_json::json!({
                    "version": version,
                    "python_prefix": probe.prefix,
                    "sidecar_package": probe.app,
                })
            ),
            0,
        )),
        _ => None,
    }
}

fn finish<R: Runtime>(app: &AppHandle<R>, state: &Mutex<Progress>, failure: Option<String>) {
    let mut s = state
        .lock()
        .unwrap_or_else(std::sync::PoisonError::into_inner);
    if s.done {
        return;
    }
    let version = app.package_info().version.to_string();
    let Some((line, code)) = verdict(failure, s.page, s.probe.as_ref(), &version) else {
        return;
    };
    s.done = true;
    println!("{line}");
    app.exit(code);
}

/// Adds the smoke run to the builder; the app then exits on its own.
pub fn attach<R: Runtime>(builder: Builder<R>) -> Builder<R> {
    let state = Arc::new(Mutex::new(Progress::default()));
    let on_load = Arc::clone(&state);
    builder
        .on_page_load(move |webview, payload| {
            if webview.label() == "main" && payload.event() == PageLoadEvent::Finished {
                on_load
                    .lock()
                    .unwrap_or_else(std::sync::PoisonError::into_inner)
                    .page = true;
                finish(webview.app_handle(), &on_load, None);
            }
        })
        .setup(move |app| {
            let handle = app.handle().clone();
            if handle.asset_resolver().get("index.html".into()).is_none() {
                finish(
                    &handle,
                    &state,
                    Some("the frontend bundle is missing".into()),
                );
                return Ok(());
            }
            let (probe_handle, probe_state) = (handle.clone(), Arc::clone(&state));
            std::thread::spawn(move || {
                let result = probe_handle
                    .path()
                    .resource_dir()
                    .map_err(|e| format!("no resource directory: {e}"))
                    .and_then(|dir| run_probe(&dir));
                match result {
                    Ok(probe) => {
                        probe_state
                            .lock()
                            .unwrap_or_else(std::sync::PoisonError::into_inner)
                            .probe = Some(probe);
                        finish(&probe_handle, &probe_state, None);
                    }
                    Err(reason) => finish(&probe_handle, &probe_state, Some(reason)),
                }
            });
            std::thread::spawn(move || {
                std::thread::sleep(TIMEOUT);
                finish(
                    &handle,
                    &state,
                    Some(format!("no verdict within {}s", TIMEOUT.as_secs())),
                );
            });
            Ok(())
        })
}

#[cfg(test)]
mod tests {
    use super::*;

    const ROOT: &str = "/pkg/Resources";

    /// A fake file system: every path exists as written, except those under `/missing`; `/pkg/link` resolves
    /// to `/elsewhere` (a link out of the package).
    fn canon(p: &Path) -> Option<PathBuf> {
        let s = p.to_str()?;
        if s.starts_with("/missing") {
            None
        } else if let Some(rest) = s.strip_prefix("/pkg/Resources/link") {
            Some(PathBuf::from(format!("/elsewhere{rest}")))
        } else {
            Some(p.to_path_buf())
        }
    }

    fn line(version: &str, prefix: &str, app: &str) -> String {
        format!(r#"{{"version": {version}, "prefix": "{prefix}", "app": "{app}"}}"#)
    }

    const PREFIX: &str = "/pkg/Resources/sidecar/python";
    const APP: &str = "/pkg/Resources/sidecar/python/lib/python3.13/site-packages/app/__init__.py";

    #[test]
    fn a_bundled_interpreter_inside_the_package_passes() {
        let got = judge(true, &line("[3, 13]", PREFIX, APP), Path::new(ROOT), canon);
        assert_eq!(
            got,
            Ok(Probe {
                prefix: PREFIX.into(),
                app: APP.into()
            })
        );
    }

    #[test]
    fn must_not_pass() {
        let site = "/usr/lib/python3.13/site-packages/app/__init__.py";
        let cases = [
            ("failed exit", false, line("[3, 13]", PREFIX, APP)),
            ("no output", true, String::new()),
            (
                "two lines",
                true,
                format!(
                    "{}\n{}",
                    line("[3, 13]", PREFIX, APP),
                    line("[3, 13]", PREFIX, APP)
                ),
            ),
            ("not json", true, "Python 3.13".into()),
            ("python 3.12", true, line("[3, 12]", PREFIX, APP)),
            (
                "python 3.13.1 as three parts",
                true,
                line("[3, 13, 1]", PREFIX, APP),
            ),
            (
                "version missing",
                true,
                format!(r#"{{"prefix": "{PREFIX}", "app": "{APP}"}}"#),
            ),
            (
                "prefix outside (system python)",
                true,
                line("[3, 13]", "/usr", APP),
            ),
            (
                "app outside (found on another path)",
                true,
                line("[3, 13]", PREFIX, site),
            ),
            (
                "app missing",
                true,
                format!(r#"{{"version": [3, 13], "prefix": "{PREFIX}"}}"#),
            ),
            (
                "prefix through a link out",
                true,
                line("[3, 13]", "/pkg/Resources/link/python", APP),
            ),
            (
                "prefix that does not exist",
                true,
                line("[3, 13]", "/missing/python", APP),
            ),
            (
                "sibling with the same prefix text",
                true,
                line("[3, 13]", "/pkg/Resources-evil/python", APP),
            ),
        ];
        for (name, ok, out) in cases {
            assert!(
                judge(ok, &out, Path::new(ROOT), canon).is_err(),
                "{name} passed"
            );
        }
    }

    fn probe() -> Probe {
        Probe {
            prefix: PREFIX.into(),
            app: APP.into(),
        }
    }

    #[test]
    fn success_needs_the_page_and_the_probe() {
        let (line, code) = verdict(None, true, Some(&probe()), "0.4.0").expect("a verdict");
        assert!(line.starts_with("FT_SMOKE_OK {"), "{line}");
        assert_eq!(code, 0);
    }

    #[test]
    fn no_success_while_a_half_is_missing() {
        assert_eq!(
            verdict(None, false, Some(&probe()), "0.4.0"),
            None,
            "page not loaded"
        );
        assert_eq!(verdict(None, true, None, "0.4.0"), None, "probe not done");
        assert_eq!(verdict(None, false, None, "0.4.0"), None, "neither");
    }

    #[test]
    fn a_failure_decides_at_once_with_a_non_zero_exit() {
        for (page, p) in [(true, Some(probe())), (false, None)] {
            let (line, code) =
                verdict(Some("x".into()), page, p.as_ref(), "0.4.0").expect("a verdict");
            assert_eq!((line.as_str(), code), ("FT_SMOKE_FAIL x", 1));
        }
    }

    #[test]
    fn a_missing_resource_directory_fails() {
        let got = judge(
            true,
            &line("[3, 13]", PREFIX, APP),
            Path::new("/missing/res"),
            canon,
        );
        assert!(got.is_err());
    }

    #[test]
    fn the_interpreter_path_is_inside_the_resources() {
        assert!(interpreter(Path::new(ROOT))
            .starts_with(Path::new(ROOT).join("sidecar").join("python")));
    }
}
