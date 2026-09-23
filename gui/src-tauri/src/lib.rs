//! The Rust side is intentionally a thin shell. All migration logic lives in the Python
//! engine (`wabridge serve`), shipped as a sidecar; the React frontend talks to it over
//! stdin/stdout through the shell plugin. Keeping Rust minimal means contributors only need
//! Python + TypeScript to change behaviour, and the app stays a small download.

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    tauri::Builder::default()
        .plugin(tauri_plugin_shell::init())
        .plugin(tauri_plugin_opener::init())
        .run(tauri::generate_context!())
        .expect("error while running WaBridge");
}
