//! Route native AppKit termination (including Dock Quit) through draft handling.
//!
//! The pinned tao delegate handles applicationWillTerminate but not
//! applicationShouldTerminate, so AppKit's terminate: bypasses Tauri's
//! RunEvent::ExitRequested. Add a subclass with exactly the same object layout;
//! every existing tao method remains inherited and no instance fields are added.
use objc2::runtime::{AnyObject, ClassBuilder, Sel};
use objc2::{rc::Retained, sel, MainThreadMarker};
use objc2_app_kit::{NSApplication, NSApplicationTerminateReply};
use std::sync::atomic::Ordering;
use std::sync::OnceLock;
use tauri::Manager;

static APP: OnceLock<tauri::AppHandle> = OnceLock::new();

extern "C-unwind" fn should_terminate(
    _delegate: &AnyObject,
    _selector: Sel,
    _application: &NSApplication,
) -> NSApplicationTerminateReply {
    if let Some(app) = APP.get() {
        if app
            .state::<super::DesktopState>()
            .allow_exit
            .load(Ordering::SeqCst)
        {
            return NSApplicationTerminateReply::TerminateNow;
        }
        super::request_close(app, true);
    }
    NSApplicationTerminateReply::TerminateCancel
}

pub fn install_termination_guard(app: &tauri::AppHandle) -> Result<(), Box<dyn std::error::Error>> {
    let marker = MainThreadMarker::new()
        .ok_or("macOS termination guard must be installed on the main thread")?;
    let application = NSApplication::sharedApplication(marker);
    let delegate = application
        .delegate()
        .ok_or("AppKit application delegate is unavailable")?;
    APP.set(app.clone())
        .map_err(|_| "macOS termination guard was installed twice")?;
    // SAFETY: ProtocolObject is an Objective-C object. The new class subclasses
    // its exact current class, adds no ivars, and installs only the standard
    // applicationShouldTerminate: signature. Installation happens once on the
    // main thread before user interaction; tao's other delegate methods inherit.
    unsafe {
        let object = Retained::cast_unchecked::<AnyObject>(delegate.clone());
        let original = object.class();
        let mut builder = ClassBuilder::new(c"SRWorkbenchAppDelegate", original)
            .ok_or("Unable to create the macOS application delegate subclass")?;
        builder.add_method(
            sel!(applicationShouldTerminate:),
            should_terminate as extern "C-unwind" fn(_, _, _) -> _,
        );
        let subclass = builder.register();
        if original.instance_size() != subclass.instance_size() {
            return Err("macOS delegate subclass changed the object layout".into());
        }
        let previous = AnyObject::set_class(&object, subclass);
        if !std::ptr::eq(previous, original) {
            return Err("macOS application delegate changed during guard installation".into());
        }
        // Refresh AppKit's optional delegate-method cache after adding the
        // termination callback. This remains the same retained delegate object.
        application.setDelegate(None);
        application.setDelegate(Some(&delegate));
    }
    Ok(())
}
