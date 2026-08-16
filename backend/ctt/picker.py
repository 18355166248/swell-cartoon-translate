"""Native folder-chooser dialog.

A browser cannot give the backend a real filesystem path: `<input type=file>`
hands back a File object with the name stripped of its directory, by design.
The usual workaround is a directory browser served by the backend, which this
project also has -- but the backend runs on the same machine as the user, so
it can simply open the operating system's own dialog and report the result.

Tkinter is tried first because it ships with Python everywhere. On Windows it
occasionally fails inside a server process (no main thread event loop), so a
PowerShell fallback drives the Win32 dialog directly.

The dialog is always spawned as a *subprocess*. Creating a Tk root inside the
uvicorn worker thread deadlocks the request: Tk requires the thread that
created a root to run its event loop, and that thread is busy serving HTTP.
"""

from __future__ import annotations

import json
import logging
import platform
import subprocess
import sys
import textwrap

log = logging.getLogger(__name__)

DIALOG_TIMEOUT = 300
"""Seconds to wait for the user. Generous -- they may go find the folder."""


_TK_SCRIPT = textwrap.dedent(
    """
    import json, sys
    try:
        import tkinter as tk
        from tkinter import filedialog
    except Exception as exc:
        print(json.dumps({"error": f"tkinter unavailable: {exc}"}))
        sys.exit(0)

    root = tk.Tk()
    root.withdraw()
    # Without this the dialog can open behind the browser window and look
    # like nothing happened.
    root.attributes("-topmost", True)
    path = filedialog.askdirectory(title=sys.argv[1] if len(sys.argv) > 1 else "选择目录",
                                   initialdir=sys.argv[2] if len(sys.argv) > 2 else None)
    root.destroy()
    print(json.dumps({"path": path or ""}))
    """
)

_PS_SCRIPT = textwrap.dedent(
    """
    Add-Type -AssemblyName System.Windows.Forms
    $dialog = New-Object System.Windows.Forms.FolderBrowserDialog
    $dialog.Description = $args[0]
    $dialog.ShowNewFolderButton = $true
    if ($args[1]) { $dialog.SelectedPath = $args[1] }
    $result = $dialog.ShowDialog()
    if ($result -eq [System.Windows.Forms.DialogResult]::OK) {
        Write-Output $dialog.SelectedPath
    }
    """
)


def _via_tkinter(title: str, initial: str) -> str | None:
    try:
        completed = subprocess.run(
            [sys.executable, "-c", _TK_SCRIPT, title, initial],
            capture_output=True,
            text=True,
            timeout=DIALOG_TIMEOUT,
        )
    except (subprocess.TimeoutExpired, OSError) as exc:
        log.warning("tkinter picker failed: %s", exc)
        return None

    for line in reversed(completed.stdout.strip().splitlines()):
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            continue
        if payload.get("error"):
            log.info("tkinter picker unavailable: %s", payload["error"])
            return None
        return payload.get("path", "")
    return None


def _via_powershell(title: str, initial: str) -> str | None:
    if platform.system() != "Windows":
        return None
    try:
        completed = subprocess.run(
            ["powershell", "-NoProfile", "-STA", "-Command", _PS_SCRIPT, title, initial],
            capture_output=True,
            text=True,
            timeout=DIALOG_TIMEOUT,
        )
    except (subprocess.TimeoutExpired, OSError) as exc:
        log.warning("powershell picker failed: %s", exc)
        return None
    return completed.stdout.strip()


def pick_folder(title: str = "选择漫画目录", initial: str = "") -> str | None:
    """Show a folder chooser and return the chosen path.

    Returns "" when the user cancels, and None when no dialog could be shown
    at all -- the caller needs to tell those apart, because the first is a
    normal outcome and the second means fall back to the in-page browser.
    """
    for backend in (_via_tkinter, _via_powershell):
        result = backend(title, initial)
        if result is not None:
            return result
    return None
