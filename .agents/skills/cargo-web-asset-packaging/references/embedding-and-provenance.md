# Embedding and provenance recipes

## MIME filtering must include required notices

The essential exception, adapted to a project's explicitly inventoried filenames:

```rust
fn notice_mime(path: &std::path::Path) -> Option<&'static str> {
    match path.file_name()?.to_str()? {
        "LICENSE" | "LICENSES.md" | "xterm-LICENSE" |
        "highlight-LICENSE" | "addon-fit-LICENSE" | "SHA256SUMS" => {
            Some("text/plain; charset=utf-8")
        }
        _ => None,
    }
}
```

Apply this before extension dispatch. This list is an example inventory, not a global permission to expose similarly named files outside the embedded public root. Serve only collected paths; return 404 for source files, `.git`, unknown paths and unsupported files. Use nosniff and an appropriate content security policy for the application.

For deterministic collection, sort normalized relative paths before generating `include_bytes!` entries into `OUT_DIR`. Emit `cargo:rerun-if-changed` for directories as well as files so additions/removals regenerate the table. Reject symlinks rather than silently embedding arbitrary external files. Avoid adding generated build output or package extraction folders to the public tree.

## Verify the result, not only the directory

For each required notice/provenance file:

```python
with urllib.request.urlopen(base_url + "/" + relative_path) as response:
    assert response.status == 200
    assert response.read() == (public_root / relative_path).read_bytes()
```

Also verify MIME, unknown/source 404s and security headers. Run this against the built executable with no static-file checkout dependency. A source license file passing a digest test is not evidence that the release executable includes it.

## Cargo notices are target-graph-specific

The source project inspected:

```sh
cargo metadata --locked --format-version 1 --filter-platform x86_64-unknown-linux-gnu
```

Use the resolved node set, exclude workspace members, and map each registry package back to its exact lockfile checksum/version. Collect supplied root license/notice files and any manifest `license_file`; preserve bytes and original filenames in the inventory. Deduplicate payloads by SHA-256, not by assuming all packages declaring MIT contain identical attribution. Reject escaping paths. Record packages with no supplied license file as unresolved rather than silently marking coverage complete.

`Cargo.lock` can contain additional packages for other targets. Say “the locked Linux target graph” if that is what was inventoried, not “every package in the lockfile.” A new dependency/target requires refreshing the inventory. Preserve upstream fonts' distinct license/attribution requirements even when all application code declares one license.

## Separate measurements

Report distinct byte counts for:

- Actually tracked source, from `git ls-files`, not an entire directory walk.
- Untracked source candidates excluding `.git`, target output, logs, caches and virtual environments.
- Public assets and Rust source as subsets.
- Optimized executable(s), generated build directories and Git metadata/history separately.

An initialized repository with all files untracked has zero tracked source bytes; do not rename the candidate total “tracked.” Candidate build directories and rollback binaries can double disk usage without changing package source size. Inspect the exact executable being shipped, not an older installed copy.
