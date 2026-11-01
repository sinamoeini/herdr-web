---
name: safe-devbox-promotion
description: "Compile, validate and promote a candidate executable on a remote development host while preserving an existing live service and backend-owned state. Includes optional AWS SSM transport and foreground-plugin restart workflows; not permission to change instance power or unrelated services."
---

# Safe Devbox Promotion

Distinguish local source, remote source, candidate executable, installed executable and running executable. Never collapse them into “the latest build.” Read-only inspection comes before mutation; supplied historical PIDs/logs may predate a reboot.

## Candidate isolation

- Confirm the intended host/account/region/profile, toolchain, selected backend and listener owner. If access fails, distinguish credentials, SSM availability and stopped instance state. Do not turn a stopped instance on merely to make progress without authorization.
- Build to a separate target/output directory. For Cargo, use `CARGO_TARGET_DIR=target/candidate` and the repository's locked build wrapper. Record the current production executable hash; do not overwrite the path its plugin manifest launches during validation.
- If reusing a build cache, avoid hardlinking candidate and production outputs. An in-place linker write can affect both names. Be explicit about which tests honor a candidate-binary override and which invoke a fixed manifest path.
- Keep reference checkouts, virtual environments, downloads, logs/screenshots and generated targets outside the delivered source inventory. Synchronization exclusions can omit changed vendor assets; inspect what actually transferred.

## Promotion transaction

Promote only within the user's authorized restart scope. If the user is checking the live instance or was promised it would remain untouched, obtain an agreed web-only restart window rather than inferring permission from “keep working.”

1. Verify the tested candidate hash and the identity of the current listener/service/backend. Stop if these differ from the deployment being authorized.
2. Save the exact running executable, installed/release copies and launch/control details in a new private rollback directory. Preserve prior deployment records instead of overwriting the only evidence.
3. Atomically replace executable paths, then restart only the identified service through its owning plugin/supervisor. Do not restart the backend to apply a frontend bridge update.
4. Require a new web process with the expected executable hash, HTTP/state health, and preservation of backend/shell identities. No test input belongs in the user's active terminal; use a separate disposable session for browser input.
5. If activation fails, use the bounded rollback plan for this service. Validate the restored version using endpoints it actually supports; new-version-only assets are not valid rollback health requirements. If process ownership is uncertain, stop and report rather than killing broadly or repeatedly restarting.
6. Leave the requested service running and record exact URL, launch method, PID, hashes, rollback location and evidence scope. Update docs from candidate to deployed only after verification.

Read [SSM transport and promotion primitives](references/ssm-and-promotion.md) when direct SSH is unavailable or implementing the transaction. This skill confers no cloud power-state, package-installation, host-key bypass, or unrelated-service permissions.

Source: Herdr Web's `docs/OPERATIONS.md`, `tools/install.sh`, isolated candidate tests, and native-plugin lifecycle. Instance IDs, AWS profiles, usernames, PIDs and ports are deployment parameters, not reusable defaults. Keep actual deployment records outside the repository.
