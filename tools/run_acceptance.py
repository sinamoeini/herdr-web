#!/usr/bin/env python3
"""Run every current Linux acceptance suite and preserve each suite's outcome."""

import argparse
import hashlib
import importlib.metadata
import json
import os
import pathlib
import platform
import shutil
import subprocess
import sys
import time

ROOT = pathlib.Path(__file__).resolve().parents[1]
SUITES = (
    "integration",
    "ui",
    "terminal_selection",
    "terminal_scroll",
    "shared_terminal",
    "graphics_browser",
    "view_edges",
    "services",
    "security",
    "resources",
    "git_failures",
    "watch_resources",
    "plugin",
    "ssh",
    "permissions",
)


def command_for(name):
    command = [sys.executable, str(ROOT / "test" / f"{name}.py")]
    return ["xvfb-run", "-a", *command] if name == "permissions" else command


def run_suites(suites, output, metadata=None):
    output.mkdir(parents=True, exist_ok=True)
    report = {
        "metadata": metadata or {},
        "scope": "Current Linux automated acceptance; not physical hardware or external-host certification.",
        "suites": [{"name": name, "command": command, "status": "pending"} for name, command in suites],
    }
    summary = output / "summary.json"
    summary.write_text(json.dumps(report, indent=2) + "\n")
    for suite in report["suites"]:
        suite["status"] = "running"
        summary.write_text(json.dumps(report, indent=2) + "\n")
        print(f'Running {suite["name"]}', flush=True)
        started = time.monotonic()
        log = output / f'{suite["name"]}.log'
        with log.open("w") as destination:
            try:
                result = subprocess.run(suite["command"], cwd=ROOT, stdout=destination, stderr=subprocess.STDOUT, check=False)
                suite["exit_code"] = result.returncode
                suite["status"] = "passed" if result.returncode == 0 else "failed"
            except OSError as error:
                destination.write(str(error) + "\n")
                suite.update(status="failed", exit_code=None, error=str(error))
        suite["seconds"] = round(time.monotonic() - started, 3)
        summary.write_text(json.dumps(report, indent=2) + "\n")
        print(log.read_text(errors="replace"), end="", flush=True)
        print(f'{suite["name"]}: {suite["status"]}', flush=True)
    failures = [suite["name"] for suite in report["suites"] if suite["status"] != "passed"]
    print(f'Acceptance: {len(report["suites"]) - len(failures)}/{len(report["suites"])} suites passed', flush=True)
    if failures:
        print("Failed suites: " + ", ".join(failures), flush=True)
    return 1 if failures else 0


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=pathlib.Path, default=ROOT / "test-results/acceptance")
    arguments = parser.parse_args()
    if sys.platform != "linux":
        parser.error("Full acceptance requires Linux, original Herdr, Chromium, Firefox, X11, PulseAudio, and OpenSSH")
    binary = ROOT / "target/release/herdr-web"
    selected = pathlib.Path(os.environ.get("HERDR_WEB_BINARY", binary)).resolve()
    if selected != binary.resolve():
        parser.error("Full acceptance must use the manifest's target/release/herdr-web, including plugin lifecycle tests")
    herdr = pathlib.Path(os.environ.get("HERDR_BIN") or shutil.which("herdr") or "")
    for executable in (binary, herdr):
        if not executable.is_file() or not os.access(executable, os.X_OK):
            parser.error(f"Missing executable: {executable}")
    for command in ("xvfb-run", "pulseaudio", "ssh", "ssh-keygen", "sudo"):
        if not shutil.which(command):
            parser.error(f"Missing acceptance prerequisite: {command}")
    try:
        playwright = importlib.metadata.version("playwright")
        backend = subprocess.check_output([str(herdr), "--version"], text=True, timeout=10).strip()
    except (importlib.metadata.PackageNotFoundError, OSError, subprocess.SubprocessError) as error:
        parser.error(str(error))
    metadata = {
        "platform": platform.platform(),
        "python": platform.python_version(),
        "playwright": playwright,
        "backend": backend,
        "backend_sha256": hashlib.sha256(herdr.read_bytes()).hexdigest(),
        "binary_sha256": hashlib.sha256(binary.read_bytes()).hexdigest(),
        "source_sha": os.environ.get("GITHUB_SHA"),
    }
    os.environ.update(HERDR_BIN=str(herdr.resolve()), HERDR_WEB_BINARY=str(binary.resolve()), SHELL="/bin/bash")
    raise SystemExit(run_suites([(name, command_for(name)) for name in SUITES], arguments.output, metadata))


if __name__ == "__main__":
    main()
