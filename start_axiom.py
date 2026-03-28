from __future__ import annotations

import argparse
import os
import shutil
import signal
import socket
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
import webbrowser
from pathlib import Path


ROOT = Path(__file__).resolve().parent
UI_DIR = ROOT / "ui"
DIST_DIR = UI_DIR / "dist"
DEFAULT_BACKEND_HOST = "127.0.0.1"
DEFAULT_BACKEND_PORT = 8765
DEFAULT_FRONTEND_HOST = "127.0.0.1"
DEFAULT_FRONTEND_PORT = 5173


class LauncherError(RuntimeError):
    pass


STOP_EVENT = threading.Event()
ACTIVE_PROCESSES: list[tuple[subprocess.Popen[str], str]] = []
ACTIVE_PROCESSES_LOCK = threading.Lock()


def port_in_use(host: str, port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as candidate:
        candidate.settimeout(0.5)
        return candidate.connect_ex((host, port)) == 0


def wait_for_http(url: str, timeout_seconds: float, process: subprocess.Popen[str] | None = None) -> None:
    deadline = time.time() + timeout_seconds
    last_error: Exception | None = None
    while time.time() < deadline:
        if process is not None and process.poll() is not None:
            raise LauncherError(f"Process exited before {url} became ready.")
        try:
            with urllib.request.urlopen(url, timeout=1.5) as response:
                if 200 <= response.status < 500:
                    return
        except Exception as error:  # noqa: BLE001
            last_error = error
        time.sleep(0.4)
    raise LauncherError(f"Timed out waiting for {url}. Last error: {last_error}")


def stream_output(process: subprocess.Popen[str], prefix: str) -> threading.Thread | None:
    if process.stdout is None:
        return None

    def _reader() -> None:
        for line in process.stdout:
            text = line.rstrip()
            if text:
                print(f"[{prefix}] {text}")

    thread = threading.Thread(target=_reader, daemon=True)
    thread.start()
    return thread


def register_process(process: subprocess.Popen[str], name: str) -> None:
    with ACTIVE_PROCESSES_LOCK:
        ACTIVE_PROCESSES.append((process, name))


def unregister_process(process: subprocess.Popen[str]) -> None:
    with ACTIVE_PROCESSES_LOCK:
        ACTIVE_PROCESSES[:] = [(candidate, name) for candidate, name in ACTIVE_PROCESSES if candidate != process]


def npm_command() -> list[str]:
    if os.name == "nt":
        command = shutil.which("npm.cmd") or shutil.which("npm")
    else:
        command = shutil.which("npm")
    if not command:
        raise LauncherError("npm was not found. Install Node.js and npm before launching Axiom UI.")
    return [command]


def check_ui_dependencies() -> None:
    package_json = UI_DIR / "package.json"
    node_modules = UI_DIR / "node_modules"
    if not package_json.exists():
        raise LauncherError("The ui/package.json file is missing.")
    if not node_modules.exists():
        raise LauncherError(
            "Frontend dependencies are not installed. Run 'cd ui && npm install' once before launching Axiom."
        )


def popen_kwargs() -> dict:
    kwargs: dict = {
        "stdout": subprocess.PIPE,
        "stderr": subprocess.STDOUT,
        "text": True,
        "bufsize": 1,
    }
    if os.name == "nt":
        kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
    return kwargs


def terminate_process(process: subprocess.Popen[str] | None, name: str) -> None:
    if process is None:
        return
    if process.poll() is not None:
        unregister_process(process)
        return
    try:
        if os.name == "nt":
            subprocess.run(
                ["taskkill", "/PID", str(process.pid), "/T", "/F"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=False,
            )
            process.wait(timeout=10)
        else:
            process.terminate()
            process.wait(timeout=5)
    except Exception:  # noqa: BLE001
        process.kill()
        process.wait(timeout=5)
    finally:
        unregister_process(process)
        print(f"[launcher] Stopped {name}.")


def shutdown_active_processes() -> None:
    with ACTIVE_PROCESSES_LOCK:
        items = list(ACTIVE_PROCESSES)
    for process, name in reversed(items):
        terminate_process(process, name)


def install_signal_handlers() -> None:
    def _handle_signal(signum, _frame) -> None:  # type: ignore[no-untyped-def]
        signal_name = getattr(signal, "Signals", None)
        readable = signal_name(signum).name if signal_name else str(signum)
        print(f"\n[launcher] Received {readable}. Shutting down Axiom...")
        STOP_EVENT.set()
        shutdown_active_processes()
        raise SystemExit(0)

    signal.signal(signal.SIGINT, _handle_signal)
    if hasattr(signal, "SIGBREAK"):
        signal.signal(signal.SIGBREAK, _handle_signal)


def start_backend(host: str, port: int) -> subprocess.Popen[str]:
    if port_in_use(host, port):
        raise LauncherError(f"Backend port {host}:{port} is already in use.")
    process = subprocess.Popen(
        [sys.executable, "ui_server.py", "--host", host, "--port", str(port)],
        cwd=str(ROOT),
        **popen_kwargs(),
    )
    register_process(process, "backend")
    stream_output(process, "backend")
    return process


def start_frontend(host: str, port: int, backend_port: int) -> subprocess.Popen[str]:
    if port_in_use(host, port):
        raise LauncherError(f"Frontend port {host}:{port} is already in use.")
    env = os.environ.copy()
    env["BROWSER"] = "none"
    env["AXIOM_BACKEND_URL"] = f"http://{DEFAULT_BACKEND_HOST}:{backend_port}"
    process = subprocess.Popen(
        npm_command() + ["run", "dev", "--", "--host", host, "--port", str(port), "--strictPort"],
        cwd=str(UI_DIR),
        env=env,
        **popen_kwargs(),
    )
    register_process(process, "frontend")
    stream_output(process, "frontend")
    return process


def run_dev(host: str, backend_port: int, frontend_port: int, open_browser: bool) -> int:
    check_ui_dependencies()
    backend = None
    frontend = None
    try:
        print("[launcher] Starting Axiom backend bridge...")
        backend = start_backend(host, backend_port)
        wait_for_http(f"http://{host}:{backend_port}/api/health", timeout_seconds=20, process=backend)
        print("[launcher] Backend is ready.")

        print("[launcher] Starting Vite frontend...")
        frontend = start_frontend(host, frontend_port, backend_port)
        wait_for_http(f"http://{host}:{frontend_port}", timeout_seconds=30, process=frontend)
        print("[launcher] Frontend is ready.")

        launch_url = f"http://{host}:{frontend_port}"
        print(f"[launcher] Axiom dev UI is running at {launch_url}")
        if open_browser:
            webbrowser.open(launch_url)
            print("[launcher] Browser opened.")

        while not STOP_EVENT.is_set():
            if backend.poll() is not None:
                raise LauncherError("Backend server exited unexpectedly.")
            if frontend.poll() is not None:
                raise LauncherError("Frontend dev server exited unexpectedly.")
            time.sleep(0.5)
        return 0
    except KeyboardInterrupt:
        print("\n[launcher] Ctrl+C received. Shutting down Axiom...")
        return 0
    finally:
        terminate_process(frontend, "frontend")
        terminate_process(backend, "backend")


def run_built(host: str, backend_port: int, open_browser: bool) -> int:
    if not DIST_DIR.exists():
        raise LauncherError("Built frontend assets were not found in ui/dist. Run 'cd ui && npm run build' first.")
    backend = None
    try:
        print("[launcher] Starting Axiom backend with built UI assets...")
        backend = start_backend(host, backend_port)
        wait_for_http(f"http://{host}:{backend_port}", timeout_seconds=20, process=backend)
        launch_url = f"http://{host}:{backend_port}"
        print(f"[launcher] Axiom built UI is running at {launch_url}")
        if open_browser:
            webbrowser.open(launch_url)
            print("[launcher] Browser opened.")

        while not STOP_EVENT.is_set():
            if backend.poll() is not None:
                raise LauncherError("Backend server exited unexpectedly.")
            time.sleep(0.5)
        return 0
    except KeyboardInterrupt:
        print("\n[launcher] Ctrl+C received. Shutting down Axiom...")
        return 0
    finally:
        terminate_process(backend, "backend")


def main() -> int:
    install_signal_handlers()
    parser = argparse.ArgumentParser(description="Start the Axiom local UI with one command.")
    parser.add_argument("--mode", choices=["dev", "built"], default="dev")
    parser.add_argument("--host", default=DEFAULT_BACKEND_HOST)
    parser.add_argument("--backend-port", type=int, default=DEFAULT_BACKEND_PORT)
    parser.add_argument("--frontend-port", type=int, default=DEFAULT_FRONTEND_PORT)
    parser.add_argument("--no-browser", action="store_true")
    args = parser.parse_args()

    try:
        if args.mode == "dev":
            return run_dev(args.host, args.backend_port, args.frontend_port, open_browser=not args.no_browser)
        return run_built(args.host, args.backend_port, open_browser=not args.no_browser)
    except LauncherError as error:
        print(f"[launcher] {error}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
