import importlib.util
import os
from pathlib import Path
import signal
import subprocess
import sys
import time


SCRIPT = Path(__file__).resolve().parents[2] / "deployment" / "render_start.py"
spec = importlib.util.spec_from_file_location("render_start", SCRIPT)
render_start = importlib.util.module_from_spec(spec)
spec.loader.exec_module(render_start)


def test_frontend_never_receives_secrets(monkeypatch):
    monkeypatch.setenv("LLM_API_KEY", "backend-only")
    monkeypatch.setenv("DATABASE_URL", "private-database")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "private-storage")
    monkeypatch.delenv("PUBLIC_ORIGIN", raising=False)
    monkeypatch.setenv("RENDER_EXTERNAL_URL", "https://aftercare.example.test")
    captured = []
    monkeypatch.setattr(render_start, "supervise", lambda commands: captured.extend(commands) or 0)
    assert render_start.main() == 0
    assert captured[0][1]["PUBLIC_ORIGIN"] == "https://aftercare.example.test"
    assert captured[1][1]["LLM_API_KEY"] == "backend-only"
    assert set(captured[2][1]) == {
        "PATH", "PORT", "API_UPSTREAM", "XDG_CONFIG_HOME", "XDG_DATA_HOME"
    }


def test_child_failure_and_shutdown_stop_other_processes(tmp_path):
    for shutdown in (False, True):
        marker = tmp_path / str(shutdown)
        child = "import pathlib, signal, sys, time; signal.signal(signal.SIGTERM, lambda *_: (pathlib.Path(sys.argv[1]).write_text('stopped'), sys.exit(0))); pathlib.Path(sys.argv[1]).write_text('ready'); time.sleep(60)"
        commands = [([sys.executable, "-c", child, str(marker)], dict(os.environ))]
        if not shutdown:
            commands.append(([sys.executable, "-c", "import time; time.sleep(0.5)"], dict(os.environ)))
        runner = subprocess.Popen([sys.executable, "-c", f"import runpy; m=runpy.run_path({str(SCRIPT)!r}); raise SystemExit(m['supervise']({commands!r}))"])
        try:
            deadline = time.monotonic() + 5
            while not marker.exists() and time.monotonic() < deadline:
                time.sleep(0.02)
            assert marker.read_text() == "ready"
            if shutdown:
                runner.send_signal(signal.SIGTERM)
            assert runner.wait(timeout=5) == (0 if shutdown else 1)
            assert marker.read_text() == "stopped"
        finally:
            if runner.poll() is None:
                runner.kill()
                runner.wait()
