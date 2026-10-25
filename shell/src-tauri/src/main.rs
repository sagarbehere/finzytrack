//! The Finzytrack desktop shell (ADR-0007). M0: an empty window over the frontend bundle; the sidecar,
//! updater and single-instance plugins arrive with their slices.
#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

fn main() {
    tauri::Builder::default()
        .run(tauri::generate_context!())
        .expect("the Tauri runtime failed to start");
}
