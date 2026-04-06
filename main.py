from __future__ import annotations

import argparse
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


def main() -> int:
    parser = argparse.ArgumentParser(description="Launch the Saxo day trader dashboard.")
    parser.add_argument("--config", default="config.yaml", help="Path to the YAML config file.")
    parser.add_argument("--port", type=int, default=8501, help="Streamlit port.")
    parser.add_argument("--headless", action="store_true", help="Run Streamlit in headless mode.")
    parser.add_argument("--no-browser", action="store_true", help="Do not auto-open a browser window.")
    parser.add_argument("--sync-only", action="store_true", help="Import the CSV into SQLite and exit.")
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
    cmd = [
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
    return subprocess.run(cmd, check=False).returncode


if __name__ == "__main__":
    raise SystemExit(main())
