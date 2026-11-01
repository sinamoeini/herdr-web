#!/usr/bin/env python3
"""Report prerequisites without installing tools, starting Herdr, or changing sessions."""

import json
import pathlib
import platform
import shutil
import importlib.util
import subprocess


def version(executable, argument="--version"):
    path = shutil.which(executable)
    if not path:
        return {"available": False}
    try:
        process = subprocess.run([path, argument], capture_output=True, text=True, timeout=10)
        return {"available": True, "path": path, "code": process.returncode, "version": (process.stdout + process.stderr).strip()}
    except (OSError, subprocess.TimeoutExpired) as error:
        return {"available": True, "path": path, "error": str(error)}


def main():
    root = pathlib.Path(__file__).resolve().parents[1]
    result = {
        "host": platform.platform(),
        "target_platform": "Linux",
        "tools": {name: version(name) for name in ("rustc", "cargo", "herdr", "git", "python3")},
        "playwright": importlib.util.find_spec("playwright") is not None,
        "cargo_lock": (root / "Cargo.lock").is_file(),
        "git_repository": (root / ".git").exists(),
        "desktop_provenance": json.loads((root / "public/desktop/UPSTREAM.json").read_text()),
        "remote_test_host": "not supplied; successful remote tests remain untested",
    }
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
