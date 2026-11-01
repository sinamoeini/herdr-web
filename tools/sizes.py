#!/usr/bin/env python3
"""Separate source candidates, actually tracked bytes, assets, and build output."""

import json
import os
import pathlib
import subprocess

ROOT = pathlib.Path(__file__).resolve().parents[1]
EXCLUDED = {".git", "target", "test-results", ".venv", "__pycache__", "artifacts", "dist", "personal", "private"}


def files(root, exclude=()):
    for directory, folders, names in os.walk(root):
        folders[:] = sorted(folder for folder in folders if folder not in exclude)
        for name in names:
            path = pathlib.Path(directory) / name
            if not path.is_symlink() and name != ".DS_Store":
                yield path


def total(root):
    return sum(path.stat().st_size for path in files(root)) if root.exists() else None


def main():
    tracked = None
    try:
        top = subprocess.check_output(["git", "-C", str(ROOT), "rev-parse", "--show-toplevel"], stderr=subprocess.DEVNULL).decode().strip()
        if pathlib.Path(top) == ROOT:
            names = subprocess.check_output(["git", "-C", str(ROOT), "ls-files", "-z"]).decode().split("\0")
            tracked = sum((ROOT / name).stat().st_size for name in names if name and (ROOT / name).is_file())
    except (OSError, subprocess.CalledProcessError):
        pass
    candidates = list(files(ROOT, EXCLUDED))
    executable = ROOT / "target/release/herdr-web"
    print(json.dumps({
        "unit": "bytes",
        "tracked_source": tracked,
        "source_candidates_excluding_generated": sum(path.stat().st_size for path in candidates),
        "candidate_file_count": len(candidates),
        "public_assets": total(ROOT / "public"),
        "rust_source": sum(path.stat().st_size for path in (ROOT / "server").rglob("*.rs")),
        "optimized_executable": executable.stat().st_size if executable.is_file() else None,
        "build_directory": total(ROOT / "target"),
        "git_metadata": total(ROOT / ".git"),
        "note": "null means unavailable/not generated, not a successful zero-size build; candidate files are not claimed tracked",
    }, indent=2))


if __name__ == "__main__":
    main()
