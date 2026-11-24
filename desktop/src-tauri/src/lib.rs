use serde_json::{json, Value};
use std::io::{BufRead, BufReader, Write};
use std::path::{Path, PathBuf};
use std::process::{Child, ChildStdin, Command, Stdio};
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::{mpsc, Arc, Mutex};
use std::time::{Duration, Instant};
use tauri::{Emitter, Manager};
use tauri_plugin_dialog::DialogExt;

#[cfg(target_os = "macos")]
mod macos;

fn error(code: &str, message: impl Into<String>) -> String {
    json!({"code": code, "message": message.into()}).to_string()
}

struct Worker {
    child: Child,
    input: Option<ChildStdin>,
    responses: mpsc::Receiver<Result<Value, String>>,
    next_id: u64,
}

impl Worker {
    fn start(resource_dir: &Path) -> Result<Self, String> {
        let mut command;
        if cfg!(debug_assertions) {
            let script =
                Path::new(env!("CARGO_MANIFEST_DIR")).join("../../scripts/sr_workbench.py");
            command = Command::new(env!("SR_DEV_PYTHON"));
            command.args(["-u", "-I"]);
            // -I excludes sibling imports, so the explicit launcher adds only
            // the trusted application scripts directory, never a workspace.
            command.arg("-c").arg(
                "import runpy,sys; p=sys.argv[1]; sys.path.insert(0, str(__import__('pathlib').Path(p).resolve().parent)); runpy.run_path(p, run_name='__main__')"
            ).arg(script);
        } else {
            let executable = resource_dir.join("worker").join(if cfg!(windows) {
                "sr-worker.exe"
            } else {
                "sr-worker"
            });
            if !executable.is_file() {
                return Err(error(
                    "WORKER_MISSING",
                    "安装包缺少 Python 服务，请重新构建完整应用。",
                ));
            }
            command = Command::new(executable);
        }
        let mut child = command
            .current_dir(resource_dir)
            .env("PYTHONUNBUFFERED", "1")
            .env("PYTHONUTF8", "1")
            .env_remove("PYTHONPATH")
            .env_remove("PYTHONHOME")
            .stdin(Stdio::piped())
            .stdout(Stdio::piped())
            .stderr(Stdio::inherit())
            .spawn()
            .map_err(|e| error("WORKER_START_FAILED", e.to_string()))?;
        let input = child.stdin.take();
        let output = child
            .stdout
            .take()
            .ok_or_else(|| error("WORKER_PIPE_FAILED", "服务输出通道不可用"))?;
        let (sender, responses) = mpsc::channel();
        std::thread::spawn(move || {
            for line in BufReader::new(output).lines() {
                let parsed = line.map_err(|e| e.to_string()).and_then(|line| {
                    serde_json::from_str::<Value>(&line)
                        .map_err(|e| format!("Invalid worker JSON: {e}"))
                });
                if sender.send(parsed).is_err() {
                    break;
                }
            }
        });
        Ok(Self {
            child,
            input,
            responses,
            next_id: 1,
        })
    }

    fn request(&mut self, method: &str, params: Value) -> Result<Value, String> {
        if let Some(status) = self
            .child
            .try_wait()
            .map_err(|e| error("WORKER_FAILED", e.to_string()))?
        {
            return Err(error(
                "WORKER_EXITED",
                format!("科研服务已退出（{status}），请重新打开工作区。"),
            ));
        }
        let id = self.next_id;
        self.next_id += 1;
        let request = json!({"id": id, "method": method, "params": params});
        let input = self
            .input
            .as_mut()
            .ok_or_else(|| error("WORKER_CLOSED", "科研服务已关闭"))?;
        writeln!(input, "{request}")
            .and_then(|_| input.flush())
            .map_err(|e| error("WORKER_WRITE_FAILED", e.to_string()))?;
        let deadline = Instant::now() + Duration::from_secs(30);
        loop {
            let remaining = deadline.saturating_duration_since(Instant::now());
            let response = self.responses.recv_timeout(remaining).map_err(|e| match e {
                mpsc::RecvTimeoutError::Timeout => json!({
                    "code": "RESULT_UNKNOWN",
                    "message": "服务响应超时。写入可能仍在完成，请通过原操作记录核对结果，勿重复提交。",
                    "details": {"request_id": id, "method": method, "operation_id": request["params"]["operation_id"]}
                }).to_string(),
                mpsc::RecvTimeoutError::Disconnected => error("WORKER_EXITED", "科研服务意外退出；尚未确认的写入请先核对原操作记录。"),
            })?.map_err(|e| error("WORKER_PROTOCOL", e))?;
            // A previous timed-out request can finish later. It must not be
            // confused with this request or automatically replayed.
            if response.get("id").and_then(Value::as_u64) != Some(id) {
                continue;
            }
            if let Some(err) = response.get("error") {
                return Err(err.to_string());
            }
            return response
                .get("result")
                .cloned()
                .ok_or_else(|| error("WORKER_PROTOCOL", "服务回复缺少 result 或 error"));
        }
    }

    fn stop(&mut self) {
        // Closing stdin asks the worker to finish its current request and exit.
        self.input.take();
        let deadline = Instant::now() + Duration::from_secs(3);
        while Instant::now() < deadline {
            match self.child.try_wait() {
                Ok(Some(_)) | Err(_) => return,
                Ok(None) => std::thread::sleep(Duration::from_millis(30)),
            }
        }
        let _ = self.child.kill();
        let _ = self.child.wait();
    }
}

impl Drop for Worker {
    fn drop(&mut self) {
        self.stop();
    }
}

#[derive(Clone)]
struct DesktopState {
    worker: Arc<Mutex<Option<Worker>>>,
    resource_dir: PathBuf,
    allow_exit: Arc<AtomicBool>,
    closing: Arc<AtomicBool>,
}

impl DesktopState {
    fn request(&self, method: &str, params: Value) -> Result<Value, String> {
        if self.closing.load(Ordering::SeqCst) {
            return Err(error("APP_CLOSING", "应用正在退出，请等待当前操作结束"));
        }
        let mut slot = self
            .worker
            .lock()
            .map_err(|_| error("WORKER_LOCK", "服务锁不可用"))?;
        if self.closing.load(Ordering::SeqCst) {
            return Err(error("APP_CLOSING", "应用正在退出，请等待当前操作结束"));
        }
        if let Some(worker) = slot.as_mut() {
            if worker.child.try_wait().ok().flatten().is_some() {
                *slot = None;
            }
        }
        if slot.is_none() {
            let mut worker = Worker::start(&self.resource_dir)?;
            let handshake = worker.request("protocol.handshake", json!({}))?;
            if handshake.get("protocol_version").and_then(Value::as_u64) != Some(1) {
                return Err(error("PROTOCOL_VERSION", "界面与科研服务的协议版本不兼容"));
            }
            *slot = Some(worker);
        }
        slot.as_mut().unwrap().request(method, params)
    }

    fn stop(&self) {
        if let Ok(mut worker) = self.worker.lock() {
            worker.take();
        }
    }
}

#[tauri::command]
async fn rpc(
    state: tauri::State<'_, DesktopState>,
    method: String,
    params: Value,
) -> Result<Value, String> {
    let service = state.inner().clone();
    tauri::async_runtime::spawn_blocking(move || service.request(&method, params))
        .await
        .map_err(|e| error("WORKER_JOIN", e.to_string()))?
}

async fn pick_directory(
    app: tauri::AppHandle,
    title: &'static str,
) -> Result<Option<String>, String> {
    let (sender, receiver) = mpsc::channel();
    app.dialog()
        .file()
        .set_title(title)
        .pick_folder(move |path| {
            let value = path
                .map(|p| {
                    p.into_path()
                        .map(|p| p.to_string_lossy().into_owned())
                        .map_err(|e| e.to_string())
                })
                .transpose();
            let _ = sender.send(value);
        });
    tauri::async_runtime::spawn_blocking(move || receiver.recv())
        .await
        .map_err(|e| error("DIALOG_FAILED", e.to_string()))?
        .map_err(|e| error("DIALOG_FAILED", e.to_string()))?
        .map_err(|e| error("DIALOG_FAILED", e))
}

#[tauri::command]
async fn choose_workspace(app: tauri::AppHandle) -> Result<Option<String>, String> {
    pick_directory(app, "选择科研工作区").await
}

#[tauri::command]
async fn choose_directory(app: tauri::AppHandle) -> Result<Option<String>, String> {
    pick_directory(app, "关联外部目录").await
}

fn system_open(value: &str) -> Result<(), String> {
    #[cfg(target_os = "macos")]
    let result = Command::new("/usr/bin/open").arg(value).status();
    #[cfg(target_os = "windows")]
    let result = Command::new("explorer.exe").arg(value).status();
    #[cfg(target_os = "linux")]
    let result = Command::new("xdg-open").arg(value).status();
    match result {
        Ok(status) if status.success() => Ok(()),
        Ok(status) => Err(error("OPEN_FAILED", format!("系统打开失败：{status}"))),
        Err(e) => Err(error("OPEN_FAILED", e.to_string())),
    }
}

fn should_reveal(path: &Path) -> bool {
    let extension = path
        .extension()
        .and_then(|x| x.to_str())
        .unwrap_or("")
        .to_ascii_lowercase();
    // Finder treats these directories as launchable bundles, not normal folders.
    if matches!(extension.as_str(), "app" | "bundle" | "workflow" | "scptd") {
        return true;
    }
    if path.is_dir() {
        return false;
    }
    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        if path
            .metadata()
            .map(|m| m.permissions().mode() & 0o111 != 0)
            .unwrap_or(false)
        {
            return true;
        }
    }
    // Unknown files and source code are revealed rather than handed to a
    // default handler that might run them. Only ordinary documents/media open.
    !matches!(
        extension.as_str(),
        "pdf"
            | "png"
            | "jpg"
            | "jpeg"
            | "webp"
            | "gif"
            | "tif"
            | "tiff"
            | "bmp"
            | "heic"
            | "txt"
            | "md"
            | "markdown"
            | "csv"
            | "tsv"
            | "json"
            | "yaml"
            | "yml"
            | "bib"
            | "log"
            | "tex"
            | "rtf"
            | "docx"
            | "xlsx"
            | "pptx"
            | "mp3"
            | "wav"
            | "m4a"
            | "mp4"
            | "mov"
    )
}

fn open_asset(path: &Path) -> Result<(), String> {
    if !should_reveal(path) {
        return system_open(&path.to_string_lossy());
    }
    #[cfg(target_os = "macos")]
    let result = Command::new("/usr/bin/open").arg("-R").arg(path).status();
    #[cfg(target_os = "windows")]
    let result = Command::new("explorer.exe")
        .arg(format!("/select,{}", path.display()))
        .status();
    #[cfg(target_os = "linux")]
    let result = Command::new("xdg-open")
        .arg(path.parent().unwrap_or(path))
        .status();
    match result {
        Ok(status) if status.success() => Ok(()),
        Ok(status) => Err(error("OPEN_FAILED", format!("文件定位失败：{status}"))),
        Err(e) => Err(error("OPEN_FAILED", e.to_string())),
    }
}

#[tauri::command]
async fn open_path(state: tauri::State<'_, DesktopState>, path: String) -> Result<(), String> {
    let service = state.inner().clone();
    tauri::async_runtime::spawn_blocking(move || {
        let resolved = service.request("files.resolve", json!({"path": path}))?;
        let allowed = resolved
            .get("path")
            .and_then(Value::as_str)
            .ok_or_else(|| error("INVALID_PATH", "服务未返回获准打开的文件路径"))?;
        open_asset(Path::new(allowed))
    })
    .await
    .map_err(|e| error("OPEN_FAILED", e.to_string()))?
}

#[tauri::command]
async fn open_url(url: String) -> Result<(), String> {
    let parsed = url::Url::parse(&url).map_err(|e| error("INVALID_URL", e.to_string()))?;
    if !matches!(parsed.scheme(), "https" | "http" | "zotero") {
        return Err(error("URL_SCHEME", "仅允许打开 http、https 或 Zotero 链接"));
    }
    tauri::async_runtime::spawn_blocking(move || system_open(parsed.as_str()))
        .await
        .map_err(|e| error("OPEN_FAILED", e.to_string()))?
}

#[tauri::command]
async fn close_window(
    app: tauri::AppHandle,
    state: tauri::State<'_, DesktopState>,
    quit: bool,
) -> Result<(), String> {
    if !quit {
        if let Some(window) = app.get_webview_window("main") {
            window
                .hide()
                .map_err(|e| error("WINDOW_CLOSE", e.to_string()))?;
        }
        return Ok(());
    }
    let service = state.inner().clone();
    state.closing.store(true, Ordering::SeqCst);
    tauri::async_runtime::spawn_blocking(move || service.stop())
        .await
        .map_err(|e| error("WORKER_STOP", e.to_string()))?;
    state.allow_exit.store(true, Ordering::SeqCst);
    app.exit(0);
    Ok(())
}

fn focus_main(app: &tauri::AppHandle) {
    if let Some(window) = app.get_webview_window("main") {
        let _ = window.show();
        let _ = window.unminimize();
        let _ = window.set_focus();
    }
}

fn request_close(app: &tauri::AppHandle, quit: bool) {
    focus_main(app);
    let _ = app.emit("workbench-close-requested", json!({"quit": quit}));
}

fn application_menu(app: &tauri::AppHandle) -> tauri::Result<tauri::menu::Menu<tauri::Wry>> {
    use tauri::menu::{Menu, MenuItem, PredefinedMenuItem, Submenu};
    let application = Submenu::with_items(
        app,
        "科研工作台",
        true,
        &[
            &PredefinedMenuItem::about(app, Some("关于科研工作台"), None)?,
            &PredefinedMenuItem::separator(app)?,
            &PredefinedMenuItem::hide(app, Some("隐藏科研工作台"))?,
            &PredefinedMenuItem::separator(app)?,
            // An ordinary item is essential: AppKit's predefined Quit directly
            // invokes terminate: and otherwise skips the frontend draft prompt.
            &MenuItem::with_id(
                app,
                "request-quit",
                "退出科研工作台",
                true,
                Some("CmdOrCtrl+Q"),
            )?,
        ],
    )?;
    let edit = Submenu::with_items(
        app,
        "编辑",
        true,
        &[
            &PredefinedMenuItem::undo(app, Some("撤销"))?,
            &PredefinedMenuItem::redo(app, Some("重做"))?,
            &PredefinedMenuItem::separator(app)?,
            &PredefinedMenuItem::cut(app, Some("剪切"))?,
            &PredefinedMenuItem::copy(app, Some("复制"))?,
            &PredefinedMenuItem::paste(app, Some("粘贴"))?,
            &PredefinedMenuItem::select_all(app, Some("全选"))?,
        ],
    )?;
    let window = Submenu::with_items(
        app,
        "窗口",
        true,
        &[
            &PredefinedMenuItem::minimize(app, Some("最小化"))?,
            &MenuItem::with_id(app, "request-close", "关闭窗口", true, Some("CmdOrCtrl+W"))?,
        ],
    )?;
    Menu::with_items(app, &[&application, &edit, &window])
}

pub fn run() {
    let app = tauri::Builder::default()
        .plugin(tauri_plugin_single_instance::init(|app, _args, _cwd| {
            focus_main(app)
        }))
        .plugin(tauri_plugin_dialog::init())
        .menu(application_menu)
        .on_menu_event(|app, event| match event.id().as_ref() {
            "request-quit" => request_close(app, true),
            "request-close" => request_close(app, false),
            _ => {}
        })
        .setup(|app| {
            let resource_dir = app.path().resource_dir()?;
            app.manage(DesktopState {
                worker: Arc::new(Mutex::new(None)),
                resource_dir,
                allow_exit: Arc::new(AtomicBool::new(false)),
                closing: Arc::new(AtomicBool::new(false)),
            });
            #[cfg(target_os = "macos")]
            macos::install_termination_guard(app.handle())?;
            Ok(())
        })
        .invoke_handler(tauri::generate_handler![
            rpc,
            choose_workspace,
            choose_directory,
            open_path,
            open_url,
            close_window
        ])
        .on_window_event(|window, event| {
            if let tauri::WindowEvent::CloseRequested { api, .. } = event {
                api.prevent_close();
                let _ = window.emit("workbench-close-requested", json!({"quit": false}));
            }
        })
        .build(tauri::generate_context!())
        .expect("无法启动科研工作台");
    app.run(|app, event| match event {
        tauri::RunEvent::ExitRequested { api, .. } => {
            let state = app.state::<DesktopState>();
            if !state.allow_exit.load(Ordering::SeqCst) {
                api.prevent_exit();
                request_close(app, true);
            }
        }
        #[cfg(target_os = "macos")]
        tauri::RunEvent::Reopen { .. } => focus_main(app),
        tauri::RunEvent::Exit => app.state::<DesktopState>().stop(),
        _ => {}
    });
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn source_code_and_application_bundles_are_never_launched() {
        for name in [
            "fit.py",
            "run.command",
            "analysis.sh",
            "code.js",
            "install.pkg",
            "image.dmg",
            "tool.app",
            "unknown.bin",
        ] {
            assert!(should_reveal(Path::new(name)), "{name}");
        }
        assert!(!should_reveal(Path::new("paper.pdf")));
    }

    #[cfg(unix)]
    #[test]
    fn executable_bit_takes_precedence_over_document_extension() {
        use std::os::unix::fs::PermissionsExt;
        let path = std::env::temp_dir().join(format!("sr-open-safety-{}.pdf", std::process::id()));
        std::fs::write(&path, b"test only").unwrap();
        std::fs::set_permissions(&path, std::fs::Permissions::from_mode(0o700)).unwrap();
        assert!(should_reveal(&path));
        std::fs::remove_file(path).unwrap();
    }
}
