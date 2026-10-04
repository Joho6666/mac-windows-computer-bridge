# Windows as a Worker / Server

Here, “server” means a private task worker reachable through SSH—not a public web service and not a Windows service running in Session 0.

## Why the worker uses an interactive logon task

The scheduled task is configured with an interactive logon principal and ordinary user privileges. This lets desktop adapters run in the signed-in user's session. Do not change it to a service account or Session 0 if the task needs UI Automation, screenshots, keyboard input, or application dialogs. Microsoft documents the restrictions on interactive services [here](https://learn.microsoft.com/en-us/windows/win32/services/interactive-services).

If nobody is signed in, the machine is asleep, or the desktop is locked, treat desktop actions as unavailable. File and background Python work may still be possible depending on whether the worker is running, but verify each operation type independently. Do not promise unattended Office automation; Microsoft documents reliability and dialog limitations [here](https://learn.microsoft.com/en-us/office/client-developer/integration/considerations-unattended-automation-office-microsoft-365-for-unattended-rpa).

## Lifecycle

- **Start:** sign in to the Windows account; Task Scheduler starts `Codex Computer Bridge Worker`.
- **Check:** inspect Task Scheduler's last-run result and use the MCP capability query. Confirm the reported host is the intended PC.
- **Stop:** stop the scheduled task before maintenance; check for running bridge jobs first.
- **Restart:** start the scheduled task after the interactive session is ready. In-progress jobs should be checked by ID; do not resubmit desktop writes blindly.
- **Uninstall:** run `scripts/remove_windows_worker.ps1`. It preserves `~/.computer-bridge` job and file data.

The Windows MCP client runs locally and uses the configured Mac SSH peer. This is the reverse direction: Mac-originated MCP calls use the Windows peer; Windows-originated MCP calls use the Mac peer. Each side can make local tool calls independently when its MCP registration is active.

## App automation

WinApp CLI is pinned to release v0.7.1 by the installer and verified by an archive SHA-256 constant. It is a UI Automation adapter, not a guarantee that every app exposes writable controls. Inspect a dedicated test window first, verify the exact window/handle, perform one controlled action, then read the value back and save to a test-only path. The [WinApp UI automation guide](https://learn.microsoft.com/en-us/windows/apps/dev-tools/winapp-cli/ui-automation) describes supported command patterns.

Do not target whichever window happens to be foreground. Avoid writing into real documents during smoke tests. Applications such as WPS may require app-specific adapters and independent validation.

## When a true headless Windows service is needed

This prototype is not a headless service. For GPU/data jobs that must run after logoff, use a separate non-UI worker design with explicit authentication, job allowlisting, resource controls, and service hardening. Keep desktop automation as a distinct user-session process and send it narrowly scoped requests over an authenticated local IPC channel.
