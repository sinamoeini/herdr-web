# Third-party notices

## Official desktop

Selected `herdrdev/herdr-app` sources/assets come from release `v0.1.11`, commit `e12fd31866f33cb613922d0b33ea506e6f23ce3c`. The original Apache-2.0 license is retained at `public/desktop/LICENSE`. `public/desktop/UPSTREAM.json` records each original/adapted digest, source path, commit, and modifications. See `PORT.md` for adapter changes and `tools/vendor.py` for repeatable transformations.

Original Herdr behavior was inspected at `herdrdev/herdr` commit `2ecf19a92fc3a7fff75b6ce3655ef04f4625ec01` (0.9.5). Its whole source/executable is not vendored or linked as a desktop dependency.

## Fonts, icons, and Herdr 98

Preserve these supplied notices with distributions, including the embedded executable:

- Geist/Geist Mono: `public/desktop/fonts/OFL.txt`.
- JetBrains Mono: `public/desktop/fonts/OFL-JetBrainsMono.txt`.
- Symbols Nerd Font: `public/desktop/fonts/LICENSE-SymbolsNerdFont.txt`.
- Pixelated MS Sans Serif, CC BY-SA 3.0: `public/desktop/mods/win98/fonts/LICENSE-MSSansSerif.txt` and `public/desktop/licenses/CC-BY-SA-3.0.txt`.
- PxPlus IBM VGA, CC BY-SA 4.0: `public/desktop/mods/win98/fonts/LICENSE-PxPlus.txt`, retaining upstream attribution/license references.
- Icons: `public/desktop/icons/LICENSES.md`, `public/desktop/icons/LICENSE-Lucide.txt`, `public/desktop/icons/LICENSE-LobeHub.txt`.

Font binaries/variants, attribution files, images, and sounds remain byte-for-byte unchanged; digests verify the font bytes. Herdr 98 components/games retain official source notices. Third-party brand marks are not owned by this project.

## Browser dependencies

xterm.js 6.0.0 and addon-fit 0.11.0 use MIT licenses; highlight.js 11.11.1 uses BSD-3-Clause. Original licenses are in `public/vendor/licenses/`. Provenance/archive hashes are in `public/vendor/UPSTREAM.json`; `public/vendor/SHA256SUMS` verifies vendored files. No CDN is used during normal operation.

## Rust and project code

New project code declares Apache-2.0; that does not relicense copied assets. Exact transitive Rust versions are in `Cargo.lock`. Original supplied license files for all 80 registry packages in the locked **Linux x86_64 dependency graph** are preserved in `public/vendor/licenses/rust/` (76 unique files, deduplicated by content). The lockfile also contains other-target packages; those are not claimed covered by this Linux release inventory. Its `UPSTREAM.json` records package versions, declared licenses, registry archive checksums, source filenames, and individual license digests. No supplied license file was missing in this inventory. These notices are embedded with the normal assets; keep them when distributing the executable. `tools/vendor_rust_notices.py` reproduces the inventory from the lockfile and Cargo's downloaded package sources without a JavaScript toolchain. This inventory does not relicense dependencies or constitute legal advice.
