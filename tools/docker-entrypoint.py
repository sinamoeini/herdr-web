#!/usr/bin/env python3
"""Own an isolated container backend and its web interface."""

import json
import os
import signal
import subprocess
import sys
import time


def wait_for_backend(backend, binary, session, timeout=30):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if backend.poll() is not None:
            raise RuntimeError(f"Herdr backend exited with status {backend.returncode}")
        try:
            result = subprocess.run(
                [binary, "session", "list", "--json"],
                capture_output=True, text=True, timeout=2, check=True,
            )
            matches = [entry for entry in json.loads(result.stdout)["sessions"] if entry["name"] == session]
            if len(matches) == 1 and matches[0].get("running") is True:
                return
        except (subprocess.SubprocessError, ValueError, KeyError, TypeError):
            pass
        time.sleep(0.1)
    raise RuntimeError(f"Herdr backend did not become ready within {timeout:g} seconds")


def stop(process):
    if process.poll() is None:
        process.terminate()
    try:
        process.wait(timeout=7)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait()


def shutdown(signum, frame):
    raise SystemExit(0)


def main():
    if len(sys.argv) > 1:
        os.execvp(sys.argv[1], sys.argv[1:])
    signal.signal(signal.SIGTERM, shutdown)
    signal.signal(signal.SIGINT, shutdown)
    binary = os.environ.get("HERDR_BIN", "/usr/local/bin/herdr")
    session = os.environ.get("HERDR_SESSION", "default")
    processes = []
    try:
        backend = subprocess.Popen([binary, "--session", session, "server"])
        processes.append(backend)
        wait_for_backend(backend, binary, session)
        web = subprocess.Popen(["/usr/local/bin/herdr-web"])
        processes.append(web)
        while True:
            for process in processes:
                if process.poll() is not None:
                    raise RuntimeError(f"Container child {process.args[0]} exited with status {process.returncode}")
            time.sleep(0.1)
    except (OSError, RuntimeError) as error:
        print(f"herdr-web container: {error}", file=sys.stderr, flush=True)
        return 1
    finally:
        signal.signal(signal.SIGTERM, signal.SIG_IGN)
        signal.signal(signal.SIGINT, signal.SIG_IGN)
        for process in reversed(processes):
            stop(process)


if __name__ == "__main__":
    sys.exit(main())
