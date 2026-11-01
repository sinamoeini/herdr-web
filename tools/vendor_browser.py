#!/usr/bin/env python3
"""One-time browser vendoring, using Python stdlib, never npm or a bundler."""

import hashlib
import io
import json
import pathlib
import tarfile
import urllib.request

ROOT = pathlib.Path(__file__).resolve().parents[1] / "public" / "vendor"
LIMIT = 20 * 1024 * 1024


def download(url):
    with urllib.request.urlopen(url, timeout=30) as response:
        content = response.read(LIMIT + 1)
    if len(content) > LIMIT:
        raise ValueError(f"Download exceeds 20 MiB: {url}")
    return content


def package(name, version, files):
    leaf = name.split("/")[-1]
    url = f"https://registry.npmjs.org/{name}/-/{leaf}-{version}.tgz"
    archive_bytes = download(url)
    with tarfile.open(fileobj=io.BytesIO(archive_bytes), mode="r:gz") as archive:
        def read(path):
            member = archive.getmember(f"package/{path}")
            if not member.isfile() or member.size > LIMIT:
                raise ValueError("Unsupported package member")
            return archive.extractfile(member).read(LIMIT + 1)
        metadata = json.loads(read("package.json"))
        if metadata["name"] != name or metadata["version"] != version:
            raise ValueError("Package identity does not match requested frozen version")
        return {destination: read(source) for source, destination in files.items()}, {"package": name, "version": version, "url": url, "archive_sha256": hashlib.sha256(archive_bytes).hexdigest()}


def main():
    staged = {}
    origins = []
    for name, version, files in [
        ("@xterm/xterm", "6.0.0", {"lib/xterm.js": "xterm.js", "css/xterm.css": "xterm.css", "LICENSE": "licenses/xterm-LICENSE"}),
        ("@xterm/addon-fit", "0.11.0", {"lib/addon-fit.js": "fit.js", "LICENSE": "licenses/addon-fit-LICENSE"}),
    ]:
        content, origin = package(name, version, files)
        staged.update(content)
        origins.append(origin)
    highlight_root = "https://raw.githubusercontent.com/highlightjs/cdn-release/11.11.1"
    staged["highlight.js"] = download(f"{highlight_root}/build/highlight.min.js")
    staged["licenses/highlight-LICENSE"] = download(f"{highlight_root}/LICENSE")
    origins.append({"package": "highlight.js", "version": "11.11.1", "source": highlight_root})
    for relative, content in staged.items():
        target = ROOT / relative
        if target.exists() and target.read_bytes() != content:
            raise ValueError(f"Refusing to overwrite modified vendor file: {relative}")
    for relative, content in sorted(staged.items()):
        target = ROOT / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)
    (ROOT / "SHA256SUMS").write_text("".join(f"{hashlib.sha256(content).hexdigest()}  {relative}\n" for relative, content in sorted(staged.items())))
    (ROOT / "UPSTREAM.json").write_text(json.dumps(origins, indent=2) + "\n")


if __name__ == "__main__":
    main()
