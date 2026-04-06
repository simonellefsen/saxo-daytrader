from __future__ import annotations

import argparse
import contextlib
import os
import signal
import subprocess
import sys
import threading
import time
import webbrowser
from pathlib import Path

ROOT = Path(__file__).resolve().parent
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from saxo_daytrader_xai.config import load_config
from saxo_daytrader_xai.importer import sync_portfolio


def _open_browser_later(port: int, delay_seconds: float = 1.5) -> None:
    def opener() -> None:
        time.sleep(delay_seconds)
        webbrowser.open(f"http://127.0.0.1:{port}")

    threading.Thread(target=opener, daemon=True).start()


def _terminate_process_group(process: subprocess.Popen[bytes] | None, *, sig: int) -> None:
    if process is None:
        return
    with contextlib.suppress(ProcessLookupError):
        os.killpg(process.pid, sig)


def _spawn_process(cmd: list[str]) -> subprocess.Popen[bytes]:
    return subprocess.Popen(cmd, start_new_session=True)


def _scheduler_restart_enabled(config: dict) -> bool:
    return bool(config.get("app", {}).get("scheduler_restart_on_failure", True))


def _scheduler_max_restarts(config: dict) -> int:
    return int(config.get("app", {}).get("scheduler_max_restarts", 3))


def _scheduler_restart_delay_seconds(config: dict) -> float:
    return float(config.get("app", {}).get("scheduler_restart_delay_seconds", 2.0))


def _wait_for_children(
    dashboard_process: subprocess.Popen[bytes],
    scheduler_process: subprocess.Popen[bytes] | None,
    *,
    scheduler_cmd: list[str] | None = None,
    scheduler_restart_enabled: bool = False,
    scheduler_max_restarts: int = 0,
    scheduler_restart_delay_seconds: float = 2.0,
) -> int:
    scheduler_restart_count = 0
    while True:
        dashboard_code = dashboard_process.poll()
        scheduler_code = scheduler_process.poll() if scheduler_process is not None else None

        if dashboard_code is not None:
            if scheduler_process is not None and scheduler_code is None:
                _terminate_process_group(scheduler_process, sig=signal.SIGTERM)
                with contextlib.suppress(subprocess.TimeoutExpired):
                    scheduler_process.wait(timeout=5)
            return int(dashboard_code)

        if scheduler_process is not None and scheduler_code not in (None, 0):
            if (
                scheduler_cmd is not None
                and scheduler_restart_enabled
                and scheduler_restart_count < scheduler_max_restarts
            ):
                scheduler_restart_count += 1
                print(
                    f"Scheduler exited with code {scheduler_code}; restarting "
                    f"({scheduler_restart_count}/{scheduler_max_restarts})...",
                    file=sys.stderr,
                )
                time.sleep(scheduler_restart_delay_seconds)
                scheduler_process = _spawn_process(scheduler_cmd)
                continue
            print("Scheduler exited unexpectedly; stopping dashboard...", file=sys.stderr)
            _terminate_process_group(dashboard_process, sig=signal.SIGTERM)
            with contextlib.suppress(subprocess.TimeoutExpired):
                dashboard_process.wait(timeout=5)
            return int(scheduler_code)

        time.sleep(0.2)


def main() -> int:
    parser = argparse.ArgumentParser(description="Launch the Saxo day trader dashboard.")
    parser.add_argument("--config", default="config.yaml", help="Path to the YAML config file.")
    parser.add_argument("--port", type=int, default=8501, help="Streamlit port.")
    parser.add_argument("--headless", action="store_true", help="Run Streamlit in headless mode.")
    parser.add_argument("--no-browser", action="store_true", help="Do not auto-open a browser window.")
    parser.add_argument("--sync-only", action="store_true", help="Import the CSV into SQLite and exit.")
    parser.add_argument("--with-scheduler", action="store_true", help="Launch the background scheduler alongside Streamlit.")
    parser.add_argument("--no-scheduler", action="store_true", help="Do not launch the background scheduler alongside Streamlit.")
    args = parser.parse_args()

    config = load_config(args.config)
    sync_result = sync_portfolio(config)
    print(
        f"Imported batch {sync_result.batch_id} from {sync_result.source_csv} "
        f"with {sync_result.imported_positions} active positions and "
        f"{sync_result.excluded_positions} exclusions."
    )

    if args.sync_only:
        return 0

    if not args.headless and not args.no_browser:
        _open_browser_later(args.port)

    app_path = ROOT / "src" / "saxo_daytrader_xai" / "ui" / "app.py"
    dashboard_cmd = [
        sys.executable,
        "-m",
        "streamlit",
        "run",
        str(app_path),
        "--server.port",
        str(args.port),
        "--server.headless",
        "true" if args.headless else "false",
        "--browser.gatherUsageStats",
        "false",
    ]
    launch_scheduler = (
        bool(args.with_scheduler or config.get("app", {}).get("launch_scheduler_with_dashboard", False))
        and not args.no_scheduler
    )
    scheduler_process = None
    scheduler_cmd = None
    if launch_scheduler:
        scheduler_cmd = [
            sys.executable,
            str(ROOT / "scripts" / "run_scheduler.py"),
            "--config",
            str(Path(args.config).resolve()),
        ]
        print("Launching background scheduler alongside dashboard...")
        scheduler_process = _spawn_process(scheduler_cmd)

    dashboard_process = _spawn_process(dashboard_cmd)
    try:
        return _wait_for_children(
            dashboard_process,
            scheduler_process,
            scheduler_cmd=scheduler_cmd,
            scheduler_restart_enabled=_scheduler_restart_enabled(config),
            scheduler_max_restarts=_scheduler_max_restarts(config),
            scheduler_restart_delay_seconds=_scheduler_restart_delay_seconds(config),
        )
    except KeyboardInterrupt:
        print("\nStopping dashboard...", file=sys.stderr)
        _terminate_process_group(dashboard_process, sig=signal.SIGTERM)
        if scheduler_process is not None:
            print("Stopping scheduler...", file=sys.stderr)
            _terminate_process_group(scheduler_process, sig=signal.SIGTERM)
        try:
            dashboard_process.wait(timeout=5)
            if scheduler_process is not None:
                with contextlib.suppress(subprocess.TimeoutExpired):
                    scheduler_process.wait(timeout=5)
            return 130
        except subprocess.TimeoutExpired:
            _terminate_process_group(dashboard_process, sig=signal.SIGKILL)
            if scheduler_process is not None:
                _terminate_process_group(scheduler_process, sig=signal.SIGKILL)
            dashboard_process.wait()
            if scheduler_process is not None:
                with contextlib.suppress(subprocess.TimeoutExpired):
                    scheduler_process.wait(timeout=2)
            return 130


if __name__ == "__main__":
    raise SystemExit(main())
