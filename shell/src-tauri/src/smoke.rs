//! `--smoke-test`: proves that a packaged build works (review:27 §1(d); ADR-0056), then exits.
//!
//! The run prints exactly one line and exits 0 only when all hold:
//! 1. the `main` window's page load reached *Finished*, and the bundle embeds the frontend's `index.html`;
//! 2. the interpreter at the package's own resource path `sidecar/python/…` (never one found on `PATH`),
//!    started with `-I`, reports isolated mode, Python 3.13, a `sys.prefix` inside the package's resource
//!    directory, and the main sidecar's package `app` imported from inside that directory;
//! 3. the packaged ledger engine (ADR-0054; S0038), started by that interpreter exactly as the sidecar starts it
//!    (`app.selfcheck`), completed a handshake with a Beancount version and answered a ping; its site-packages
//!    lie inside the resource directory; and the two sides are apart (ADR-0054 decision 5): the main side finds no
//!    `beancount`, `beanquery` or `ledger_engine`, the engine side does not find `app`, no entry of the engine's
//!    path lies in the main side's site-packages, and no entry of the main side's path lies in the engine's site.
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
const PROBE: &str = "import json, sys\nimport app\nfrom app.selfcheck import report_packaged\nprint(json.dumps({'version': list(sys.version_info[:2]), 'isolated': sys.flags.isolated, 'prefix': sys.prefix, 'app': app.__file__, 'engine': report_packaged()}))\n";
/// Packages of the engine that the main side must never find (ADR-0054 decision 5).
const ENGINE_ONLY: [&str; 3] = ["beancount", "beanquery", "ledger_engine"];

/// What the bundled interpreter reported, once it passed every check.
#[derive(Debug, PartialEq, Eq)]
pub struct Probe {
    pub prefix: PathBuf,
    pub app: PathBuf,
    pub engine_site: PathBuf,
    pub beancount: String,
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
    if v["isolated"] != serde_json::json!(1) {
        return Err("the bundled interpreter did not run isolated (-I)".into());
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
    let prefix = inside("prefix")?;
    let app = inside("app")?;
    let (engine_site, beancount) = judge_engine(&v["engine"], &root, &canon)?;
    Ok(Probe {
        prefix,
        app,
        engine_site,
        beancount,
    })
}

/// The strings of a JSON list, or an error naming the field.
fn strings<'a>(engine: &'a Value, key: &str) -> Result<Vec<&'a str>, String> {
    engine[key]
        .as_array()
        .and_then(|a| a.iter().map(Value::as_str).collect::<Option<Vec<_>>>())
        .ok_or(format!("engine field {key} missing"))
}

/// Judges the engine half of the probe (`app.selfcheck`): returns the engine's site and its Beancount version.
fn judge_engine(
    engine: &Value,
    root: &Path,
    canon: &impl Fn(&Path) -> Option<PathBuf>,
) -> Result<(PathBuf, String), String> {
    if !engine.is_object() {
        return Err("the probe did not report the bundled engine".into());
    }
    if let Some(error) = engine["error"].as_str() {
        return Err(format!("the bundled engine failed: {error}"));
    }
    if !engine["error"].is_null() {
        return Err("the bundled engine reported a malformed error".into());
    }
    let beancount = engine["handshake"]["beancount_version"]
        .as_str()
        .filter(|s| !s.is_empty())
        .ok_or("the bundled engine gave no handshake with a Beancount version")?;
    if engine["pong"] != Value::Bool(true) {
        return Err("the bundled engine did not answer a ping".into());
    }
    let site = engine["site"]
        .as_str()
        .and_then(|s| canon(Path::new(s)))
        .ok_or("the bundled engine's site does not exist")?;
    if !site.starts_with(root) {
        return Err("the bundled engine's site is outside the package".into());
    }
    for name in ENGINE_ONLY {
        if engine["main_finds"][name] != Value::Bool(false) {
            return Err(format!("the main side finds the engine's {name}"));
        }
    }
    if engine["engine_finds_app"] != Value::Bool(false) {
        return Err("the bundled engine finds the main sidecar's app".into());
    }
    let main_sites: Vec<PathBuf> = strings(engine, "main_sites")?
        .into_iter()
        .filter_map(|s| canon(Path::new(s)))
        .collect();
    if main_sites.is_empty() {
        return Err("the main side reported no site-packages".into());
    }
    for entry in strings(engine, "engine_path")? {
        if let Some(p) = canon(Path::new(entry)) {
            if main_sites.iter().any(|m| p.starts_with(m)) {
                return Err("the bundled engine's path holds the main side's site-packages".into());
            }
        }
    }
    for entry in strings(engine, "main_path")? {
        if canon(Path::new(entry)).is_some_and(|p| p.starts_with(&site)) {
            return Err("the main side's path holds the bundled engine's site".into());
        }
    }
    Ok((site, beancount.to_string()))
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
                    "engine_site": probe.engine_site,
                    "beancount": probe.beancount,
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

    const PREFIX: &str = "/pkg/Resources/sidecar/python";
    const APP: &str = "/pkg/Resources/sidecar/python/lib/python3.13/site-packages/app/__init__.py";
    const MAIN_SITE: &str = "/pkg/Resources/sidecar/python/lib/python3.13/site-packages";
    const ENGINE_SITE: &str = "/pkg/Resources/sidecar/engine/site-packages";
    const STDLIB: &str = "/pkg/Resources/sidecar/python/lib/python3.13";

    /// The engine half of a passing probe (`app.selfcheck`'s report).
    fn engine() -> Value {
        serde_json::json!({
            "site": ENGINE_SITE,
            "handshake": {"protocol": 1, "engine_version": "0.4.0.dev0", "beancount_version": "3.2.0"},
            "pong": true,
            "error": null,
            "main_finds": {"beancount": false, "beanquery": false, "ledger_engine": false},
            "main_path": [STDLIB, MAIN_SITE],
            "main_sites": [MAIN_SITE],
            "engine_finds_app": false,
            "engine_path": [STDLIB, ENGINE_SITE],
        })
    }

    fn line_with(version: &str, prefix: &str, app: &str, engine: &Value) -> String {
        format!(
            r#"{{"version": {version}, "isolated": 1, "prefix": "{prefix}", "app": "{app}", "engine": {engine}}}"#
        )
    }

    fn line(version: &str, prefix: &str, app: &str) -> String {
        line_with(version, prefix, app, &engine())
    }

    #[test]
    fn a_bundled_interpreter_inside_the_package_passes() {
        let got = judge(true, &line("[3, 13]", PREFIX, APP), Path::new(ROOT), canon);
        assert_eq!(
            got,
            Ok(Probe {
                prefix: PREFIX.into(),
                app: APP.into(),
                engine_site: ENGINE_SITE.into(),
                beancount: "3.2.0".into(),
            })
        );
    }

    #[test]
    fn a_failed_engine_is_named_with_its_reason() {
        // packaged.yml's must-not-pass run expects this reason when the engine's site is moved away.
        let mut e = engine();
        e["error"] = "start_failed: handshake: crashed: the engine exited with code 1".into();
        let got = judge(
            true,
            &line_with("[3, 13]", PREFIX, APP, &e),
            Path::new(ROOT),
            canon,
        );
        assert_eq!(
            got,
            Err("the bundled engine failed: start_failed: handshake: crashed: the engine exited with code 1".into())
        );
    }

    #[test]
    fn engine_must_not_pass() {
        let edits: [(&str, &str, Value); 22] = [
            ("no engine report", "", Value::Null),
            (
                "an error",
                "error",
                "start_failed: handshake: crashed".into(),
            ),
            ("a malformed error", "error", serde_json::json!(1)),
            ("no handshake", "handshake", Value::Null),
            (
                "an empty Beancount version",
                "handshake",
                serde_json::json!({"protocol": 1, "beancount_version": ""}),
            ),
            ("no pong", "pong", false.into()),
            ("pong as text", "pong", "true".into()),
            (
                "site outside",
                "site",
                "/elsewhere/engine/site-packages".into(),
            ),
            (
                "site through a link out",
                "site",
                "/pkg/Resources/link/engine".into(),
            ),
            ("site missing", "site", "/missing/engine".into()),
            (
                "main finds beancount",
                "main_finds",
                serde_json::json!({"beancount": true, "beanquery": false, "ledger_engine": false}),
            ),
            (
                "main finds beanquery",
                "main_finds",
                serde_json::json!({"beancount": false, "beanquery": true, "ledger_engine": false}),
            ),
            (
                "main finds the engine",
                "main_finds",
                serde_json::json!({"beancount": false, "beanquery": false, "ledger_engine": true}),
            ),
            ("main finds unreported", "main_finds", serde_json::json!({})),
            ("engine finds app", "engine_finds_app", true.into()),
            ("engine view missing", "engine_finds_app", Value::Null),
            (
                "engine path holds the main site",
                "engine_path",
                serde_json::json!([ENGINE_SITE, MAIN_SITE]),
            ),
            (
                "engine path holds a folder in the main site",
                "engine_path",
                serde_json::json!([ENGINE_SITE, format!("{MAIN_SITE}/app")]),
            ),
            ("engine path missing", "engine_path", Value::Null),
            (
                "main path holds the engine site",
                "main_path",
                serde_json::json!([MAIN_SITE, ENGINE_SITE]),
            ),
            ("main sites missing", "main_sites", serde_json::json!([])),
            (
                "main sites that do not exist",
                "main_sites",
                serde_json::json!(["/missing/site"]),
            ),
        ];
        for (name, key, value) in edits {
            let mut e = engine();
            if key.is_empty() {
                e = value;
            } else {
                e[key] = value;
            }
            let out = line_with("[3, 13]", PREFIX, APP, &e);
            assert!(
                judge(true, &out, Path::new(ROOT), canon).is_err(),
                "{name} passed"
            );
        }
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
                "not isolated (-I dropped)",
                true,
                line("[3, 13]", PREFIX, APP).replace(r#""isolated": 1"#, r#""isolated": 0"#),
            ),
            (
                "isolation flag missing",
                true,
                line("[3, 13]", PREFIX, APP).replace(r#""isolated": 1, "#, ""),
            ),
            (
                "version missing",
                true,
                format!(
                    r#"{{"prefix": "{PREFIX}", "app": "{APP}", "engine": {}}}"#,
                    engine()
                ),
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
                format!(
                    r#"{{"version": [3, 13], "isolated": 1, "prefix": "{PREFIX}", "engine": {}}}"#,
                    engine()
                ),
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
            engine_site: ENGINE_SITE.into(),
            beancount: "3.2.0".into(),
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
