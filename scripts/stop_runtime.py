from __future__ import annotations

import contextlib
import os
import signal
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
RUNTIME_DIR = ROOT / ".run"
PID_FILES = {
    "dashboard": RUNTIME_DIR / "dashboard.pid",
    "scheduler": RUNTIME_DIR / "scheduler.pid",
    "launcher": RUNTIME_DIR / "launcher.pid",
}


def _read_pid(path: Path) -> int | None:
    if not path.exists():
        return None
    try:
        return int(path.read_text(encoding="utf-8").strip())
    except (OSError, ValueError):
        return None


def _process_exists(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _terminate_pid(pid: int, *, name: str, sig: int) -> bool:
    try:
        os.killpg(pid, sig)
        print(f"Sent signal {sig} to {name} process group {pid}.")
        return True
    except ProcessLookupError:
        print(f"{name.capitalize()} process group {pid} no longer exists.")
        return False
    except PermissionError:
        with contextlib.suppress(ProcessLookupError):
            os.kill(pid, sig)
            print(f"Sent signal {sig} to {name} process {pid}.")
            return True
        print(f"Permission denied while stopping {name} process {pid}.")
        return False


def _remove_pid_file(path: Path) -> None:
    with contextlib.suppress(FileNotFoundError):
        path.unlink()


def _fallback_candidate_pids() -> list[tuple[int, str]]:
    try:
        output = subprocess.check_output(
            ["ps", "-ax", "-o", "pid=,command="],
            text=True,
        )
    except (OSError, subprocess.CalledProcessError):
        return []

    candidates: list[tuple[int, str]] = []
    markers = (
        str(ROOT / "main.py"),
        str(ROOT / "scripts" / "run_scheduler.py"),
        str(ROOT / "src" / "saxo_daytrader_xai" / "ui" / "app.py"),
        "streamlit run",
    )
    for line in output.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        try:
            pid_text, command = stripped.split(None, 1)
            pid = int(pid_text)
        except ValueError:
            continue
        if pid == os.getpid():
            continue
        if any(marker in command for marker in markers) and str(ROOT) in command:
            candidates.append((pid, command))
    return candidates


def main() -> int:
    stopped_any = False
    for name in ("dashboard", "scheduler", "launcher"):
        path = PID_FILES[name]
        pid = _read_pid(path)
        if pid is None:
            continue
        if not _process_exists(pid):
            _remove_pid_file(path)
            continue
        if _terminate_pid(pid, name=name, sig=signal.SIGTERM):
            stopped_any = True
        _remove_pid_file(path)

    tracked_pids = {pid for path in PID_FILES.values() if (pid := _read_pid(path)) is not None}
    for pid, command in _fallback_candidate_pids():
        if pid in tracked_pids:
            continue
        if _terminate_pid(pid, name="fallback", sig=signal.SIGTERM):
            print(f"Matched fallback process {pid}: {command}")
            stopped_any = True

    if not stopped_any:
        print("No tracked runtime processes were running.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
