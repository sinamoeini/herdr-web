#!/usr/bin/env python3
"""Package the Linux x86_64 release with a binary-ready plugin manifest."""

import argparse
import gzip
import hashlib
import io
import os
import pathlib
import re
import tarfile
import tempfile
import tomllib

ROOT = pathlib.Path(__file__).resolve().parents[1]
TARGET = "x86_64-unknown-linux-gnu"
DOCUMENTS = (
    "LICENSE",
    "README.md",
    "CONTRIBUTING.md",
    "THIRD_PARTY_NOTICES.md",
    "PORT.md",
    "docs/GUIDE.md",
    "docs/OPERATIONS.md",
    "docs/ACCEPTANCE.md",
    "docs/API.md",
    "docs/GIT.md",
    "docs/RELEASING.md",
)
PROVENANCE = (
    "public/desktop/UPSTREAM.json",
    "public/vendor/UPSTREAM.json",
    "public/vendor/SHA256SUMS",
)


def plugin_manifest(root, tag):
    cargo = tomllib.loads((root / "server/Cargo.toml").read_text())
    source = (root / "herdr-plugin.toml").read_text()
    plugin = tomllib.loads(source)
    version = cargo["package"]["version"]
    if not re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+(?:-[0-9A-Za-z.-]+)?", version):
        raise ValueError("Unsupported package version")
    if tag != f"v{version}" or plugin["version"] != version:
        raise ValueError("Release tag, Cargo version, and plugin version must agree")
    if plugin.get("platforms") != ["linux"]:
        raise ValueError("Only Linux release packaging is supported")
    if plugin.get("build") != [{"command": ["./build.sh"]}]:
        raise ValueError("Review the source build hook before packaging")
    if {action["id"] for action in plugin["actions"]} != {"start", "stop", "restart", "status"}:
        raise ValueError("Unexpected plugin actions")
    for action in plugin["actions"]:
        if action["command"] != ["./target/release/herdr-web", "plugin", action["id"]]:
            raise ValueError("Unexpected source plugin command")
    packaged = source.replace('\n[[build]]\ncommand = ["./build.sh"]\n', "", 1)
    packaged = packaged.replace('"./target/release/herdr-web"', '"./herdr-web"')
    parsed = tomllib.loads(packaged)
    if "build" in parsed or any(
        action["command"] != ["./herdr-web", "plugin", action["id"]]
        for action in parsed["actions"]
    ):
        raise ValueError("Could not create a binary-only plugin manifest")
    return packaged.encode()


def asset(root, relative):
    path = root / relative
    if path.is_symlink() or not path.is_file():
        raise ValueError(f"Missing or symlinked release input: {relative}")
    path.resolve().relative_to(root.resolve())
    return path.read_bytes()


def package(root, binary, output, tag, epoch=0):
    manifest = plugin_manifest(root, tag)
    if binary.is_symlink() or not binary.is_file() or not os.access(binary, os.X_OK):
        raise ValueError("Release binary must be a regular executable, not a symlink")
    executable = binary.read_bytes()
    if executable[:6] != b"\x7fELF\x02\x01" or executable[18:20] != b"\x3e\x00":
        raise ValueError("Release binary must be a little-endian Linux x86_64 ELF executable")
    payloads = {name: asset(root, name) for name in DOCUMENTS + PROVENANCE}
    for directory in (root / "public/desktop", root / "public/vendor/licenses"):
        for path in sorted(directory.rglob("*")):
            if path.is_symlink():
                raise ValueError(f"Symlink in notice inventory: {path}")
            relative = path.relative_to(root).as_posix()
            if path.is_file() and (
                directory.name == "licenses"
                or path.name.upper().startswith(("LICENSE", "LICENCE", "OFL", "COPYING", "NOTICE"))
                or relative.startswith("public/desktop/licenses/")
            ):
                payloads[relative] = asset(root, relative)
    payloads["herdr-plugin.toml"] = manifest
    payloads["herdr-web"] = executable
    prefix = f"herdr-web-{tag}-{TARGET}"
    output.mkdir(parents=True, exist_ok=True)
    archive = output / f"{prefix}.tar.gz"
    checksums = output / "SHA256SUMS"
    if archive.exists() or checksums.exists():
        raise ValueError("Release outputs already exist; choose a fresh output directory")
    with tempfile.TemporaryDirectory(prefix="herdr-web-package-") as temporary:
        staged = pathlib.Path(temporary) / archive.name
        with staged.open("wb") as raw:
            with gzip.GzipFile(filename="", mode="wb", fileobj=raw, mtime=epoch) as compressed:
                with tarfile.open(fileobj=compressed, mode="w", format=tarfile.PAX_FORMAT) as bundle:
                    for name, content in sorted(payloads.items()):
                        entry = tarfile.TarInfo(f"{prefix}/{name}")
                        entry.size = len(content)
                        entry.mode = 0o755 if name == "herdr-web" else 0o644
                        entry.mtime = epoch
                        bundle.addfile(entry, io.BytesIO(content))
        content = staged.read_bytes()
        digest = hashlib.sha256(content).hexdigest()
        with archive.open("xb") as destination:
            destination.write(content)
        with checksums.open("x") as destination:
            destination.write(f"{digest}  {archive.name}\n")
    return archive


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tag", required=True)
    parser.add_argument("--binary", type=pathlib.Path, default=ROOT / "target/release/herdr-web")
    parser.add_argument("--output", type=pathlib.Path, default=ROOT / "dist")
    arguments = parser.parse_args()
    try:
        epoch = int(os.environ.get("SOURCE_DATE_EPOCH", "0"))
        if not 0 <= epoch <= 0xFFFFFFFF:
            raise ValueError("SOURCE_DATE_EPOCH must fit an unsigned 32-bit timestamp")
        archive = package(ROOT, arguments.binary, arguments.output, arguments.tag, epoch)
    except (OSError, ValueError, KeyError) as error:
        parser.error(str(error))
    print(archive)


if __name__ == "__main__":
    main()
