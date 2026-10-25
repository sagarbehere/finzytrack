//! The Finzytrack desktop shell (ADR-0007). M0: an empty window over the frontend bundle, and `--smoke-test`
//! for packaged builds (`smoke`); the sidecar, updater and single-instance plugins arrive with their slices.
#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

mod smoke;

fn main() {
    let builder = tauri::Builder::default();
    let builder = if std::env::args().skip(1).any(|a| a == smoke::FLAG) {
        smoke::attach(builder)
    } else {
        builder
    };
    builder
        .run(tauri::generate_context!())
        .expect("the Tauri runtime failed to start");
}
