---
name: cargo-web-asset-packaging
description: "Package a browser interface into a standalone Cargo-built executable with pinned upstream assets, native JavaScript modules, reproducible provenance and embedded license notices. Use when a Cargo-only or no-JavaScript-bundler build is required."
---

# Cargo Web Asset Packaging

Honor the selected build architecture. A Cargo-only constraint does not mean no JavaScript: native browser modules and vendored runtime libraries can be embedded without Node/npm/Bun. Do not impose this architecture on projects that already deliberately use a JS build pipeline.

## Reusable workflow

1. Acquire exact upstream references outside the deliverable. Inventory only required panels/layouts/icons/fonts/sounds and licenses; do not vendor entire repositories as runtime assets.
2. Hash original bytes before transformation. Record source repository/commit/path, destination, original digest, adapted digest, and named transformations. Preserve font/binary bytes and notices. Change the importer, then regenerate; do not hand-edit generated copies and leave provenance stale.
3. Rewrite bare imports to local browser runtime modules with a repeatable, tested transform. Keep adapter-owned paths protected from importer overwrite and traversal.
4. Generate a sorted embedded asset table from an explicit public directory during Cargo build. Emit directory/file change-tracking directives, reject symlinks, exclude temporary package/build/test folders, and serve unknown paths as 404. Never fall back to serving the checkout.
5. **Audit extensionless files.** Extension-only MIME filters can omit `LICENSE`, dependency `*-LICENSE`, `SHA256SUMS`, or icon attribution Markdown even though the source tree looks complete. Explicitly include the intended notices as plain text.
6. Verify the executable outside the checkout and fetch embedded notices byte-for-byte over HTTP. Preserve the locked dependency graph's supplied notices, not just browser package licenses.

Read [embedding and provenance recipes](references/embedding-and-provenance.md) for the notice exception, dependency graph inventory and size reporting.

Keep application code/license declarations separate from third-party licenses. Record target-specific dependency coverage accurately. Do not claim a license inventory is a legal audit or that a build requiring no JS tool proves Node was absent from the host.

Source: Herdr Web `server/build.rs`, `tools/{vendor,vendor_browser,vendor_rust_notices,sizes}.py`, `test/{security,test_tools}.py`, and the extensionless-license deployment regression.
