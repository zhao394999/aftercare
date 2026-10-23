"""Run the API, durable database worker and web server on one free instance."""

import os
import signal
import subprocess
import sys
import time


def supervise(commands):
    children = []

    def stop(*_):
        raise SystemExit(0)

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    try:
        for command, environment in commands:
            children.append(subprocess.Popen(command, env=environment, start_new_session=True))
        # A worker failure must restart the instance instead of leaving a broken UI.
        os.wait()
        return 1
    finally:
        for child in children:
            if child.poll() is None:
                os.killpg(child.pid, signal.SIGTERM)
        deadline = time.monotonic() + 20
        for child in children:
            try:
                child.wait(timeout=max(0.1, deadline - time.monotonic()))
            except subprocess.TimeoutExpired:
                os.killpg(child.pid, signal.SIGKILL)
                child.wait()


def main():
    environment = dict(os.environ)
    if not environment.get("PUBLIC_ORIGIN"):
        environment["PUBLIC_ORIGIN"] = environment["RENDER_EXTERNAL_URL"]
    # The static web server receives no database, storage or AI credentials.
    edge_environment = {
        "PATH": os.defpath,
        "PORT": environment.get("PORT", "10000"),
        "API_UPSTREAM": "127.0.0.1:8000",
        "XDG_CONFIG_HOME": "/tmp/caddy-config",
        "XDG_DATA_HOME": "/tmp/caddy-data",
    }
    return supervise([
        ([sys.executable, "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", "8000", "--workers", "1", "--no-access-log"], environment),
        ([sys.executable, "-m", "app.worker"], environment),
        (["/usr/bin/caddy", "run", "--config", "/etc/caddy/Caddyfile", "--adapter", "caddyfile"], edge_environment),
    ])


if __name__ == "__main__":
    sys.exit(main())
