//! Keep hidden Windows conversions and their descendants owned by the caller.
//!
//! Ctrl+C can terminate the CLI without unwinding Rust destructors, and hidden
//! Pandoc/SVG children do not receive its console event. A non-inherited job
//! handle closes on caller exit, stopping only this conversion's process tree.

use std::io;
use std::process::{Child, Command};

/// Retain one conversion job until completion, cancellation, or worker shutdown.
pub(super) struct ProcessTree {
    #[cfg(windows)]
    _job: std::os::windows::io::OwnedHandle,
}

impl ProcessTree {
    /// Assign the suspended child before it can spawn untracked grandchildren.
    pub(super) fn spawn(command: &mut Command) -> io::Result<(Child, Self)> {
        #[cfg(windows)]
        {
            use std::os::windows::io::AsRawHandle;
            use std::os::windows::process::CommandExt;
            use windows_sys::Win32::System::JobObjects::AssignProcessToJobObject;
            use windows_sys::Win32::System::Threading::{CREATE_NO_WINDOW, CREATE_SUSPENDED};

            let tree = Self {
                _job: windows_job()?,
            };
            command.creation_flags(CREATE_NO_WINDOW | CREATE_SUSPENDED);
            let mut child = command.spawn()?;
            // SAFETY: Both handles are live and owned throughout assignment.
            let assigned = unsafe {
                AssignProcessToJobObject(tree._job.as_raw_handle(), child.as_raw_handle())
            };
            let started = if assigned == 0 {
                Err(io::Error::last_os_error())
            } else {
                resume_child(child.id())
            };
            if let Err(error) = started {
                // A failed assignment/resume must never leave a suspended process behind.
                let _ = child.kill();
                let _ = child.wait();
                return Err(error);
            }
            Ok((child, tree))
        }
        #[cfg(not(windows))]
        {
            Ok((command.spawn()?, Self {}))
        }
    }
}

/// Create a non-inheritable job so only its Rust owner keeps descendants alive.
#[cfg(windows)]
fn windows_job() -> io::Result<std::os::windows::io::OwnedHandle> {
    use std::os::windows::io::{AsRawHandle, FromRawHandle, OwnedHandle};
    use windows_sys::Win32::System::JobObjects::{
        CreateJobObjectW, JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE, JOBOBJECT_EXTENDED_LIMIT_INFORMATION,
        JobObjectExtendedLimitInformation, SetInformationJobObject,
    };

    // SAFETY: Null attributes create a non-inheritable handle; the information
    // pointer refers to a live, correctly sized Win32 structure for this call.
    unsafe {
        let raw = CreateJobObjectW(std::ptr::null(), std::ptr::null());
        if raw.is_null() {
            return Err(io::Error::last_os_error());
        }
        let job = OwnedHandle::from_raw_handle(raw);
        let mut limits: JOBOBJECT_EXTENDED_LIMIT_INFORMATION = std::mem::zeroed();
        limits.BasicLimitInformation.LimitFlags = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE;
        if SetInformationJobObject(
            job.as_raw_handle(),
            JobObjectExtendedLimitInformation,
            (&limits as *const JOBOBJECT_EXTENDED_LIMIT_INFORMATION).cast(),
            std::mem::size_of_val(&limits) as u32,
        ) == 0
        {
            return Err(io::Error::last_os_error());
        }
        Ok(job)
    }
}

/// Resume the only thread of a newly created suspended child using stable Win32 APIs.
#[cfg(windows)]
fn resume_child(pid: u32) -> io::Result<()> {
    use std::os::windows::io::{AsRawHandle, FromRawHandle, OwnedHandle};
    use windows_sys::Win32::Foundation::INVALID_HANDLE_VALUE;
    use windows_sys::Win32::System::Diagnostics::ToolHelp::{
        CreateToolhelp32Snapshot, TH32CS_SNAPTHREAD, THREADENTRY32, Thread32First, Thread32Next,
    };
    use windows_sys::Win32::System::Threading::{OpenThread, ResumeThread, THREAD_SUSPEND_RESUME};

    // SAFETY: The snapshot and thread handles remain owned, and enumeration uses
    // an initialized Win32 structure. The child cannot create another thread yet.
    unsafe {
        let raw = CreateToolhelp32Snapshot(TH32CS_SNAPTHREAD, 0);
        if raw == INVALID_HANDLE_VALUE {
            return Err(io::Error::last_os_error());
        }
        let snapshot = OwnedHandle::from_raw_handle(raw);
        let mut entry: THREADENTRY32 = std::mem::zeroed();
        entry.dwSize = std::mem::size_of_val(&entry) as u32;
        let mut found = Thread32First(snapshot.as_raw_handle(), &mut entry);
        while found != 0 {
            if entry.th32OwnerProcessID == pid {
                let raw = OpenThread(THREAD_SUSPEND_RESUME, 0, entry.th32ThreadID);
                if raw.is_null() {
                    return Err(io::Error::last_os_error());
                }
                let thread = OwnedHandle::from_raw_handle(raw);
                if ResumeThread(thread.as_raw_handle()) == u32::MAX {
                    return Err(io::Error::last_os_error());
                }
                return Ok(());
            }
            found = Thread32Next(snapshot.as_raw_handle(), &mut entry);
        }
        Err(io::Error::new(
            io::ErrorKind::NotFound,
            "Suspended child thread was not found",
        ))
    }
}

#[cfg(all(test, windows))]
mod tests {
    use super::ProcessTree;
    use std::path::{Path, PathBuf};
    use std::process::{Child, Command, Stdio};
    use std::time::{Duration, Instant};

    /// Reuse the test executable as real owner, worker, and SVG-like descendants.
    fn fixture_command(role: &str, ready: &Path) -> Command {
        let mut command = Command::new(std::env::current_exe().unwrap());
        command
            .args([
                "--exact",
                "process_tree::tests::process_fixture",
                "--nocapture",
            ])
            .env("PAPPER_PROCESS_TREE_TEST_ROLE", role)
            .env("PAPPER_PROCESS_TREE_TEST_READY", ready)
            .stdin(Stdio::null())
            .stdout(Stdio::null())
            .stderr(Stdio::null());
        command
    }

    /// Publish readiness only after the deepest child has started, then stay alive.
    #[test]
    fn process_fixture() {
        let Ok(role) = std::env::var("PAPPER_PROCESS_TREE_TEST_ROLE") else {
            return;
        };
        let ready = PathBuf::from(std::env::var_os("PAPPER_PROCESS_TREE_TEST_READY").unwrap());
        let _tree;
        let _child;
        if role == "owner" {
            let (child, tree) = ProcessTree::spawn(&mut fixture_command("worker", &ready)).unwrap();
            _child = child;
            _tree = Some(tree);
        } else if role == "worker" {
            // This ordinary grandchild must inherit membership without a job handle.
            _child = fixture_command("leaf", &ready).spawn().unwrap();
            _tree = None;
        } else {
            std::fs::write(&ready, std::process::id().to_string()).unwrap();
            loop {
                std::thread::sleep(Duration::from_secs(1));
            }
        }
        loop {
            std::thread::sleep(Duration::from_secs(1));
        }
    }

    /// Reap diagnostic subprocesses even when the regression assertion fails.
    struct Fixture(Child);

    impl Drop for Fixture {
        /// Stop only the subprocess created by this test.
        fn drop(&mut self) {
            let _ = self.0.kill();
            let _ = self.0.wait();
        }
    }

    /// Wait for a real descendant PID with a bounded deadline and early-exit checks.
    fn wait_ready(child: &mut Child, ready: &Path) -> u32 {
        let deadline = Instant::now() + Duration::from_secs(10);
        loop {
            if let Ok(text) = std::fs::read_to_string(ready)
                && let Ok(pid) = text.parse()
            {
                return pid;
            }
            assert!(
                child.try_wait().unwrap().is_none(),
                "Fixture exited before readiness"
            );
            assert!(Instant::now() < deadline, "Fixture did not become ready");
            std::thread::sleep(Duration::from_millis(10));
        }
    }

    /// Killing the caller must stop hidden grandchildren and spare unrelated work.
    #[test]
    fn owner_termination_reaps_descendants_without_killing_other_processes() {
        use std::os::windows::io::{AsRawHandle, FromRawHandle, OwnedHandle};
        use windows_sys::Win32::Foundation::WAIT_OBJECT_0;
        use windows_sys::Win32::System::Threading::{
            OpenProcess, PROCESS_SYNCHRONIZE, WaitForSingleObject,
        };

        let temporary = tempfile::tempdir().unwrap();
        let ready = temporary.path().join("descendant.pid");
        let unrelated_ready = temporary.path().join("unrelated.pid");
        let mut unrelated = Fixture(fixture_command("leaf", &unrelated_ready).spawn().unwrap());
        wait_ready(&mut unrelated.0, &unrelated_ready);
        let mut owner = Fixture(fixture_command("owner", &ready).spawn().unwrap());
        let pid = wait_ready(&mut owner.0, &ready);
        // SAFETY: OpenProcess creates an owned synchronization handle for the
        // known fixture PID; waiting observes termination without PID reuse races.
        let descendant = unsafe {
            let raw = OpenProcess(PROCESS_SYNCHRONIZE, 0, pid);
            assert!(!raw.is_null(), "Cannot observe descendant process");
            OwnedHandle::from_raw_handle(raw)
        };
        owner.0.kill().unwrap();
        owner.0.wait().unwrap();
        assert_eq!(
            unsafe { WaitForSingleObject(descendant.as_raw_handle(), 5000) },
            WAIT_OBJECT_0
        );
        assert!(
            unrelated.0.try_wait().unwrap().is_none(),
            "Unrelated process was terminated"
        );
    }
}
