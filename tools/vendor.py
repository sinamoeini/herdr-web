#!/usr/bin/env python3
"""Inspect pinned local checkouts and import an explicitly reviewed asset plan."""

import argparse
import hashlib
import json
import pathlib
import re
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
DESKTOP = "e12fd31866f33cb613922d0b33ea506e6f23ce3c"
HERDR = "2ecf19a92fc3a7fff75b6ce3655ef04f4625ec01"
IMPORTS = {
    "gpui-kit": "/desktop/runtime/gpui.js",
    "gpui-base": "/desktop/runtime/base.js",
    "gpui": "/desktop/runtime/gpui.js",
    "base": "/desktop/runtime/base.js",
    "herdr": "/desktop/runtime/herdr.js",
}
EXTENSIONS = {".js", ".json", ".css", ".svg", ".png", ".jpg", ".jpeg", ".gif", ".webp", ".woff", ".woff2", ".ttf", ".otf", ".wav", ".mp3", ".ogg", ".txt"}


def git(checkout, *arguments):
    return subprocess.check_output(["git", "-C", str(checkout), *arguments], stderr=subprocess.PIPE)


def checkout_files(checkout, commit):
    checkout = pathlib.Path(checkout).resolve(strict=True)
    if checkout == ROOT or ROOT in checkout.parents:
        raise ValueError("Reference checkouts must be outside the deliverable tree")
    actual = git(checkout, "rev-parse", "HEAD").decode().strip()
    if actual != commit:
        raise ValueError(f"Expected frozen commit {commit}; checkout is {actual}")
    paths = git(checkout, "ls-tree", "-rz", "--name-only", commit).decode().split("\0")
    return checkout, [path for path in paths if path]


def blob(checkout, commit, path):
    return git(checkout, "show", f"{commit}:{path}")


def digest(data):
    return hashlib.sha256(data).hexdigest()


def adapt_imports(data):
    text = data.decode("utf-8")
    pattern = r"(\bfrom\s*|\bimport\s*\(?\s*)([\"'])(gpui-kit|gpui-base|gpui|base|herdr)(\2)"
    return re.sub(pattern, lambda match: match[1] + match[2] + IMPORTS[match[3]] + match[4], text).encode("utf-8")


def safe_destination(value):
    path = pathlib.PurePosixPath(value)
    if path.is_absolute() or ".." in path.parts or not path.parts or path.parts[0] not in {"frame", "mods", "layouts", "themes", "fonts", "icons", "sounds", "licenses", "keys.json", "keys.macos.json", "programs.json"}:
        raise ValueError(f"Asset destination is outside approved desktop groups: {value}")
    return path


def inventory(arguments):
    desktop, desktop_paths = checkout_files(arguments.desktop, DESKTOP)
    herdr, herdr_paths = checkout_files(arguments.herdr, HERDR)
    required = [
        "src/protocol/wire.rs", "src/protocol/endpoint.rs", "src/client/handshake.rs", "src/client/terminal_setup.rs",
        "src/app/api/plugins/runtime.rs", "src/app/api/plugins/env.rs", "src/app/api/plugins/manifest.rs",
        "src/app/api/plugins/panes.rs", "src/app/api/plugins/context.rs", "src/cli/plugin.rs", "src/plugin_paths.rs",
    ]
    missing = set(required) - set(herdr_paths)
    if missing:
        raise ValueError(f"Pinned Herdr checkout lacks required sources: {sorted(missing)}")
    candidates = [path for path in desktop_paths if path.startswith("defaults/") or pathlib.PurePosixPath(path).suffix in {".ttf", ".otf", ".woff", ".woff2"} or any(word in pathlib.PurePosixPath(path).name.upper() for word in ("LICENSE", "COPYING", "OFL"))]
    result = {
        "desktop_commit": DESKTOP,
        "herdr_commit": HERDR,
        "desktop_candidates": [],
        "herdr_required_sources": [],
        "review_required": "Read required sources and inventory panel controls before backend integration. This tool does not certify review.",
    }
    for path in candidates:
        data = blob(desktop, DESKTOP, path)
        entry = {"path": path, "sha256": digest(data), "bytes": len(data)}
        if path.endswith(".js"):
            source = data.decode("utf-8")
            entry["import_specifiers"] = sorted(set(re.findall(r"\bfrom\s*[\"']([^\"']+)", source)))
            entry["method_candidates"] = sorted(set(re.findall(r"\.([A-Za-z_][A-Za-z_0-9]*)\s*\(", source)))
        result["desktop_candidates"].append(entry)
    for path in required:
        data = blob(herdr, HERDR, path)
        result["herdr_required_sources"].append({"path": path, "sha256": digest(data), "bytes": len(data)})
    pathlib.Path(arguments.output).write_text(json.dumps(result, indent=2) + "\n")


def import_assets(arguments):
    checkout, paths = checkout_files(arguments.desktop, DESKTOP)
    plan = json.loads(pathlib.Path(arguments.plan).read_text())
    if plan.get("desktop_commit") != DESKTOP or not plan.get("files"):
        raise ValueError("A reviewed, nonempty asset plan for the exact desktop commit is required")
    if "LICENSE" not in paths:
        raise ValueError("Upstream LICENSE is missing; do not substitute a guessed license")
    staged = [("LICENSE", "LICENSE", blob(checkout, DESKTOP, "LICENSE"), False)]
    staged.append(("src/settings.rs", "themes/palettes.json", blob(checkout, DESKTOP, "src/settings.rs"), False))
    destinations = {"LICENSE"}
    for entry in plan["files"]:
        source = entry["source"]
        destination = str(safe_destination(entry["destination"]))
        if source not in paths or destination in destinations:
            raise ValueError(f"Missing source or duplicate destination: {entry}")
        if pathlib.PurePosixPath(destination).suffix not in EXTENSIONS and not any(word in pathlib.PurePosixPath(destination).name.upper() for word in ("LICENSE", "COPYING", "OFL", "ATTRIBUTION")):
            raise ValueError(f"Unsupported asset type: {destination}")
        destinations.add(destination)
        staged.append((source, destination, blob(checkout, DESKTOP, source), destination.endswith(".js")))
    output = ROOT / "public" / "desktop"
    previous_manifest = json.loads((output / "UPSTREAM.json").read_text()) if (output / "UPSTREAM.json").exists() else {}
    previous_files = {entry["destination"]: entry.get("adapted_sha256") for entry in previous_manifest.get("files", [])}
    records = []
    writes = []
    for source, destination, original, adapt in staged:
        original_hash = digest(original)
        content = adapt_imports(original) if adapt else original
        changes = ["bare runtime imports rewritten"] if content != original else []
        if source == "src/settings.rs":
            palettes = {}
            for name in ["GRAPHITE", "INK", "MOSS", "PAPER"]:
                section = re.search(r"const " + name + r": Palette = Palette \{(.*?)\n\};", original.decode(), re.S).group(1)
                palette = {field: "#" + re.search(field + r": 0x([0-9a-f]+)", section).group(1) for field in ["background", "foreground", "selection"]}
                palette["ansi"] = ["#" + value for value in re.findall(r"0x([0-9a-f]+)", re.search(r"ansi: \[(.*?)\]", section, re.S).group(1))]
                palettes[name.lower()] = palette
            content = (json.dumps(palettes, indent=2) + "\n").encode()
            changes = ["extract immutable built-in theme palettes into browser JSON; original Rust backend is not vendored"]
        if adapt:
            text = content.decode()
            if "This computer" in text:
                text = text.replace("This computer", "Local server")
                changes.append("current machine described as Local server")
            if "props()" in text:
                text = text.replace("props()", "this.webProps")
                changes.append("ambient props replaced by instance-owned webProps")
            if "watchFolder(" in text:
                text = re.sub(r"watchFolder\((root|repo)\)", r"watchFolder(\1, this)", text)
                changes.append("folder watches carry instance cancellation")
            if source == "defaults/mods/files/main.js":
                start = text.index("async function directory(")
                end = text.index("export default class Files", start)
                text = 'import { listDirectory } from "/desktop/runtime/herdr.js";\n' + text[:start] + 'async function directory(path) { return (await listDirectory(path)).entries; }\n\n' + text[end:]
                changes.append("shell directory listing replaced by bounded Rust file API")
            text = text.replace("cx.timer.every(10000,", "cx.timer.every(60000,")
            if source == "defaults/mods/win98/main.js":
                text = text.replace("this.role = R ? new R() : null;", "this.role = R ? new R() : null;\n    if (this.role) { this.role.webProps = pr; this.role.abortController = this.abortController; this.role.own = this.own.bind(this); this.own(() => this.role?.release?.()); }")
                changes.append("delegated Herdr 98 roles receive instance-owned browser props and cancellation")
            if source == "defaults/mods/win98/menu.js":
                text = text.replace("  release() {", '  release() {\n    if (shared("win98.start") === this.webProps?.overlay) setShared("win98.start", null);\n    if (this.sub) { const id = this.sub.id; this.sub = null; closeTree(id); }')
                changes.append("browser overlay disposal releases Start state and owned submenus")
            if source == "defaults/mods/win98/taskbar.js":
                start = text.index('        const names = AGENTS.map(')
                ending = '        this.installed = r.stdout.split("\\n").filter(Boolean);'
                end = text.index(ending, start) + len(ending)
                text = 'import { installedPrograms } from "/desktop/runtime/herdr.js";\n' + text[:start] + '        this.installed = await installedPrograms();' + text[end:]
                changes.append("installed-agent detection uses the Rust catalog instead of a native shell loop")
            if source == "defaults/mods/win98/kit.js":
                text = text.replace("window.paint_path(", "appWindow().paint_path(")
                changes.append("ambient native path painting uses the browser window adapter for gradients/checkerboards")
            if "nextCommand()" in text:
                text = text.replace("nextCommand()", "nextCommand(this)")
                changes.append("browser command waiting is cancelled with the owning panel")
            if source == "defaults/frame/settings/main.js":
                text = text.replace('.child(nav.h_full().child(div().flex_1()).child(this.button(t, "st-feedback", "Send feedback", () => { closeOverlay(this.webProps?.overlay); run("app.feedback"); })))', '.child(nav.h_full().child(div().flex_1()))')
                text = text.replace("Herdr looks for both every hour and tells you in the corner. herdr-core runs your terminals, so they keep running through its updates.", "Local server: original Herdr owns your terminals. Updates are manual. No automatic updater runs.")
                text = text.replace("Want your own sounds? Ask your agent to set sounds.done and sounds.blocked in settings.json to an MP3.", "Bundled notification sounds obey browser audio permissions and require an enabling user gesture.")
                text = text.replace('this.heading(t, "herdr-core")', 'this.heading(t, "Original Herdr")').replace('`herdr-core ${c.version ?? ""}`', '`Herdr ${c.version ?? ""}`')
                start = text.index("  privacy(t) {")
                end = text.index("  // ----------------------------------------------------------------------", start)
                text = text[:start] + '  privacy(t) { return v_flex().w_full().child(this.heading(t, "Privacy")).child(this.row(t, "Telemetry disabled", "Herdr Web collects and sends no telemetry. Feedback submission is disabled.", div().child("Off"))); }\n\n' + text[end:]
                changes.append("manual updates, original backend labels, disabled telemetry, removed feedback action")
            if source == "defaults/mods/machine-add/main.js":
                text = text.replace("await this.checkMac(target);", "")
                text = text.replace('"Couldn\'t open a terminal for it. Run `hc machine add " + target + "` in one."', '"SSH forwarding requires existing keys and trusted known_hosts. Configure SSH outside Herdr Web, then retry."')
                start=text.index('      } else if (["hc_missing"')
                end=text.index('      await this.refresh();',start)
                text=text[:start]+'      } else this.state[target] = why.message || "SSH forwarding failed";\n'+text[end:]
                text=text.replace("A VPS, a Mac mini, a box at home: Herdr reaches it over ssh, and agents there keep running while your laptop sleeps.","Use your existing SSH keys and known_hosts. The remote machine must already run Herdr Web on loopback. Each connection opens a separate browser window.")
                text=text.replace('    this.input = InputState.new', '''    this.port = InputState.new({ value: "8000" });
    this.portError = "";
    this.port.on("change", (_e, cx) => { this.portError = ""; cx.notify(); });
    this.port.on("submit", (_e, cx) => this.add(this.typed, cx));
    this.input = InputState.new''', 1)
                text=text.replace('      cx.timer.after(600, () => this.input.focus());', '')
                text=text.replace('    return this.added.some((m) => m.target === target || m.name === target || m.host === target);',
                                  '    return this.added.some((m) => m.host === target && m.port === Number(this.port.value()));')
                text=text.replace('    target = target.trim();', '''    const rawPort = this.port.value().trim();
    const port = Number(rawPort);
    if (!/^[0-9]+$/.test(rawPort) || !Number.isInteger(port) || port < 1 || port > 65535) {
      this.portError = "Enter a remote web port from 1 to 65535.";
      cx.notify();
      return;
    }
    this.portError = "";
    target = target.trim();''', 1)
                text=text.replace('["machine", "add", target, "--json"]', '["machine", "add", target, "--port", String(port), "--json"]')
                text=text.replace('.child("Added")', '.child(`Connected · web port ${this.port.value()}`)')
                text=text.replace('  render() {', '''  connections(t) {
    return v_flex().id("ma-connections").gap(6).children(this.added.map(m =>
      h_flex().gap(8).items_center()
        .child(div().flex_1().text_size(12).child(`${m.host} · remote web port ${m.port}`))
        .child(div().id(`ma-disconnect-${m.host}-${m.port}`).cursor_pointer().text_size(12)
          .on_click((_e, cx) => cx.spawn(async cx => {
            const r = await hc(["machine", "disconnect", m.host, "--port", String(m.port)]);
            if (r.code) this.portError = r.stderr;
            await this.refresh(); cx.notify();
          })).child("Disconnect"))
    ));
  }

  render() {''')
                text=text.replace('      .child(div().pt(16).pb(6)', '''      .child(v_flex().pt(12).gap(6)
        .child(div().text_size(12).child("Remote web port"))
        .child(Input.new(this.port).attr("aria-label", "Remote web port").attr("inputmode", "numeric")
          .attr("aria-describedby", "ma-port-help").attr("aria-invalid", String(Boolean(this.portError)))
          .h(34).px(10).rounded(8).border_1().border_color(t.input).bg(t.background).text_color(t.foreground))
        .child(div().id("ma-port-help").text_size(12).text_color(t.muted)
          .child("Default: 8000. SSH uses the port in your SSH configuration."))
        .when(Boolean(this.portError), el => el.child(div().id("ma-port-error").attr("role", "alert")
          .text_size(12).text_color(t.blocked).child(this.portError))))
      .child(this.connections(t))
      .child(div().pt(16).pb(6)''', 1)
                text=text.replace('Input.new(this.input)', 'Input.new(this.input).attr("aria-label", "SSH destination")')
                changes.append("SSH failures describe browser forwarding requirements rather than native core setup")
                changes.append("validated remote web port, port-specific connection status and disconnect controls")
            if source == "defaults/mods/changes/main.js":
                text = text.replace('"Commit"', '"Stage all & commit"')
                text = text.replace('  setLines(lines) {', '  setLines(lines) {\n    lines = lines.slice(0, 1500);')
                changes.append("commit control explicitly discloses staging all changes")
                changes.append("diff parsing and maximum-width measurement are bounded to 1500 rows")
            if source == "defaults/mods/menu/main.js":
                text = text.replace('["Zoom", "pane.zoom", { pane: tg.pane }],', '["Zoom", "pane.zoom", { pane: tg.pane }],\n        ["Rename", "pane.rename", { pane: tg.pane }],\n        ["New child agent…", "terminal.child", { parent: tg.terminal }],\n        ["Park terminal", "terminal.park", { terminal: tg.terminal }],')
                changes.append("pane menu exposes rename, child-agent launch, and persistent parking through bounded adapter operations")
            if source == "defaults/mods/new-menu/main.js":
                text = text.replace('    const space = spaces().find(s => s.active);', '    const space = spaces().find(s => s.active);\n    const parent = this.webProps?.parent;\n    const launch = args => run(parent ? "terminal.child" : "tab.new", { ...args, ...(parent ? { parent, argv: args.argv || [] } : {}) });')
                text = text.replace('run("tab.new", {', 'launch({')
                text = text.replace('.child(div().h(1).mx(4).my(5).bg(t.border))', '.when(!parent, el => el.child(div().h(1).mx(4).my(5).bg(t.border))')
                text = text.replace('action: "open" } }), "", Boolean(space)));', 'action: "open" } }), "", Boolean(space))));')
                changes.append("agent picker launches children for its explicit parent and hides unrelated space/worktree actions")
            if source == "defaults/mods/agents/main.js":
                text = text.replace('.child("Agents")', '.child(this.webProps?.parked ? "Parked terminals" : "Agents")')
                changes.append("reuse agent tree component for the parked-terminal overlay")
            if source == "defaults/mods/picker/main.js":
                text = text.replace('.id("pk-list")', '.id("pk-list").relative()')
                changes.append("position virtual picker rows relative to their browser list container")
            content = text.encode()
        target = output / destination
        if target.exists() and target.read_bytes() != content and digest(target.read_bytes()) != previous_files.get(destination):
            raise ValueError(f"Refusing to overwrite an existing adapted file: {target}")
        records.append({"source": source, "destination": destination, "commit": DESKTOP, "original_sha256": original_hash, "adapted_sha256": digest(content), "changes": changes})
        writes.append((target, content))
    for target, content in writes:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)
    manifest = {"status": "imported; panel compatibility and font/license completeness require review", "desktop_repository": "https://github.com/herdrdev/herdr-app", "desktop_commit": DESKTOP, "herdr_commit": HERDR, "files": records}
    (output / "UPSTREAM.json").write_text(json.dumps(manifest, indent=2) + "\n")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    subcommands = parser.add_subparsers(dest="action", required=True)
    inspect = subcommands.add_parser("inventory")
    inspect.add_argument("--desktop", required=True)
    inspect.add_argument("--herdr", required=True)
    inspect.add_argument("--output", required=True)
    inspect.set_defaults(run=inventory)
    importer = subcommands.add_parser("import")
    importer.add_argument("--desktop", required=True)
    importer.add_argument("--plan", required=True)
    importer.set_defaults(run=import_assets)
    arguments = parser.parse_args()
    try:
        arguments.run(arguments)
    except (OSError, ValueError, KeyError, subprocess.CalledProcessError) as error:
        parser.exit(1, f"Asset import failed: {error}\n")


if __name__ == "__main__":
    main()
