use std::process::Command;

fn main() {
    // This is only used in debug builds; installed builds exclusively use the
    // interpreter frozen into resources/worker, independent of PATH and cwd.
    let python = std::env::var("SR_DEV_PYTHON").unwrap_or_else(|_| {
        Command::new("python3")
            .args(["-c", "import sys; print(sys.executable)"])
            .output()
            .ok()
            .filter(|output| output.status.success())
            .map(|output| String::from_utf8_lossy(&output.stdout).trim().to_owned())
            .unwrap_or_else(|| "/usr/bin/python3".to_owned())
    });
    println!("cargo:rustc-env=SR_DEV_PYTHON={python}");
    println!("cargo:rerun-if-env-changed=SR_DEV_PYTHON");
    tauri_build::build()
}
