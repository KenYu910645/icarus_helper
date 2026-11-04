"""Headless world archiving and retention, independent of desktop windows."""
from __future__ import annotations

import base64
from contextlib import contextmanager
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time


TASK_NAME = "IcarusHelperWorldBackups"


def _now():
    return datetime.now(timezone.utc).isoformat()


def _write_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(value, handle, ensure_ascii=False, indent=2)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


@contextmanager
def _singleton(root):
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    handle = (root / "daemon.lock").open("a+b", buffering=0)
    acquired = False
    try:
        if handle.seek(0, os.SEEK_END) == 0:
            handle.write(b"0")
        handle.seek(0)
        try:
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
            acquired = True
        except OSError:
            pass
        yield acquired
    finally:
        if acquired:
            handle.seek(0)
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl
                fcntl.flock(handle, fcntl.LOCK_UN)
        handle.close()


class BackupRunner:
    """One tick is independently testable without processes or real save paths."""

    def __init__(self, store):
        self.store = store
        self.signatures = {}
        self.last_checks = {}
        self.last_prunes = {}
        self.state = {"schema_version": 1, "pid": os.getpid(), "started_at_utc": _now(), "worlds": {}}

    def tick(self, moment=None):
        moment = time.monotonic() if moment is None else moment
        try:
            worlds = self.store.worlds()
            self.state.pop("error", None)
        except Exception as exc:
            self.state["error"] = str(exc)
            worlds = []
        for world in worlds:
            ident = world["world_id"]
            status = self.state["worlds"].setdefault(ident, {})
            capture_error = None
            try:
                stat = Path(world["source"]).stat()
                signature = (stat.st_mtime_ns, stat.st_size, stat.st_ino)
                if signature != self.signatures.get(ident) or moment - self.last_checks.get(ident, -100) >= 10:
                    result = self.store.capture(ident)
                    self.signatures[ident] = signature
                    self.last_checks[ident] = moment
                    status["last_success"] = {"at_utc": _now(), "result": result}
            except Exception as exc:
                capture_error = str(exc)
                status["last_error"] = {"at_utc": _now(), "message": capture_error}
                self.signatures.pop(ident, None)
            # Retention also runs while the game is closed or its source is offline.
            if moment - self.last_prunes.get(ident, -100) >= 60:
                try:
                    result = self.store.prune(ident)
                    status["last_prune"] = {"at_utc": _now(), "result": result}
                    self.last_prunes[ident] = moment
                    status.pop("prune_error", None)
                except Exception as exc:
                    status["prune_error"] = {"at_utc": _now(), "message": str(exc)}
            if capture_error is None:
                status.pop("last_error", None)
        self.state["heartbeat_at_utc"] = _now()
        _write_json(self.store.root / "daemon-status.json", self.state)
        return self.state


def run(root=None, *, once=False, poll_seconds=1):
    from .world_backups import BackupStore
    store = BackupStore(root=root)
    with _singleton(store.root) as acquired:
        if not acquired:
            return 0
        runner = BackupRunner(store)
        while True:
            runner.tick()
            if once:
                return 0
            time.sleep(max(0.2, poll_seconds))


def launch_command(root):
    if getattr(sys, "frozen", False):
        command = [sys.executable]
    else:
        executable = Path(sys.executable)
        windowless = executable.with_name("pythonw.exe")
        command = [str(windowless if os.name == "nt" and windowless.is_file() else executable),
                   str(Path(__file__).resolve().parent.parent / "main.py")]
    return [*command, "--backup-daemon", "--backup-root", str(Path(root).resolve())]


def _register_task(command):
    """Current-user, limited task; use structured JSON through an encoded script."""
    if os.name != "nt":
        return False
    payload = base64.b64encode(json.dumps(command, ensure_ascii=False).encode("utf-8")).decode("ascii")
    script = """$ErrorActionPreference = 'Stop'
$taskCommand = [Text.Encoding]::UTF8.GetString([Convert]::FromBase64String('PAYLOAD')) | ConvertFrom-Json
$taskName = 'IcarusHelperWorldBackups'
$taskArguments = ($taskCommand | Select-Object -Skip 1 | ForEach-Object { '"' + $_ + '"' }) -join ' '
$taskExisting = Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
if ($taskExisting -and $taskExisting.Actions.Execute -eq $taskCommand[0] -and $taskExisting.Actions.Arguments -eq $taskArguments -and $taskExisting.State -ne 'Disabled') { exit 0 }
$taskUser = [Security.Principal.WindowsIdentity]::GetCurrent().Name
$taskAction = New-ScheduledTaskAction -Execute $taskCommand[0] -Argument $taskArguments
$taskLogon = New-ScheduledTaskTrigger -AtLogOn -User $taskUser
$taskRecovery = New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(1) -RepetitionInterval (New-TimeSpan -Minutes 1)
$taskPrincipal = New-ScheduledTaskPrincipal -UserId $taskUser -LogonType Interactive -RunLevel Limited
$taskSettings = New-ScheduledTaskSettingsSet -MultipleInstances IgnoreNew -ExecutionTimeLimit ([TimeSpan]::Zero) -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable -Hidden
Register-ScheduledTask -TaskName $taskName -Action $taskAction -Trigger @($taskLogon,$taskRecovery) -Principal $taskPrincipal -Settings $taskSettings -Description 'ICARUS Helper world save backups and retention cleanup' -Force | Out-Null
""".replace("PAYLOAD", payload)
    encoded = base64.b64encode(script.encode("utf-16-le")).decode("ascii")
    result = subprocess.run([str(Path(os.environ["SYSTEMROOT"]) / "System32/WindowsPowerShell/v1.0/powershell.exe"),
                             "-NoProfile", "-NonInteractive", "-EncodedCommand", encoded],
                            capture_output=True, timeout=25,
                            creationflags=subprocess.CREATE_NO_WINDOW)
    if result.returncode:
        raise RuntimeError("Unable to configure automatic backups at sign-in: " + result.stderr.decode(errors="replace")[-700:])
    return True


def ensure_running(root=None, *, install_task=True):
    from .world_backups import BackupStore
    store = BackupStore(root=root)
    command = launch_command(store.root)
    installation_error = None
    if install_task:
        try:
            _register_task(command)
        except Exception as exc:
            installation_error = str(exc)
    # Each process takes daemon.lock itself, so concurrent desktop windows cannot
    # create concurrent writers. A stale heartbeat never suppresses a restart.
    kwargs = {"stdin": subprocess.DEVNULL, "stdout": subprocess.DEVNULL, "stderr": subprocess.DEVNULL}
    if os.name == "nt":
        kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW | subprocess.DETACHED_PROCESS
    process = subprocess.Popen(command, **kwargs)
    result = {"requested_at_utc": _now(), "launcher_pid": process.pid,
              "command": command, "scheduled_task": TASK_NAME if install_task and not installation_error else None,
              "error": installation_error}
    _write_json(store.root / "service-installation.json", result)
    return result
