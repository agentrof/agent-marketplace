"""Submit an owner notification through the current operating system."""
from __future__ import annotations

import json
import platform
import shutil
import subprocess


def send(title: str, message: str) -> dict:
    system = platform.system()
    data = {"title": title[:200], "message": message[:4000]}
    if system == "Darwin":
        executable = shutil.which("osascript")
        script = "on run argv\ndisplay notification (item 2 of argv) with title (item 1 of argv)\nend run"
        command = [executable, "-e", script, data["title"], data["message"]]
        stdin = None
    elif system == "Linux":
        executable = shutil.which("notify-send")
        command = [executable, "--", data["title"], data["message"]]
        stdin = None
    elif system == "Windows":
        executable = shutil.which("powershell.exe")
        script = ("$p = [Console]::In.ReadToEnd() | ConvertFrom-Json; "
                  "Add-Type -AssemblyName System.Windows.Forms; "
                  "$n = New-Object System.Windows.Forms.NotifyIcon; "
                  "$n.Icon = [System.Drawing.SystemIcons]::Information; $n.Visible = $true; "
                  "$n.ShowBalloonTip(8000, $p.title, $p.message, "
                  "[System.Windows.Forms.ToolTipIcon]::Info); Start-Sleep -Seconds 8; $n.Dispose()")
        command = [executable, "-NoProfile", "-NonInteractive", "-Command", script]
        stdin = json.dumps(data)
    else:
        return {"submitted": False, "reason": "unsupported operating system"}
    if executable is None:
        return {"submitted": False, "reason": "desktop notification command is unavailable"}
    try:
        result = subprocess.run(command, input=stdin, text=True, capture_output=True, timeout=15, check=False)
    except (OSError, subprocess.TimeoutExpired):
        return {"submitted": False, "reason": "desktop notification command failed"}
    return {"submitted": result.returncode == 0, "backend": system,
            **({"reason": "desktop notification command refused"} if result.returncode else {})}
