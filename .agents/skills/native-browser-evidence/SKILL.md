---
name: native-browser-evidence
description: "Design or repair Python Playwright acceptance tests for real terminals, clipboard/audio permissions, browser event routing, and cancellation/resource ownership. Use when mocked responses or synthetic events cannot establish the requested behavior."
---

# Native Browser Evidence

Choose the evidence boundary before choosing the automation. A DOM handler test, browser permission test, operating-system integration test, and physical-device test answer different questions. Report them separately.

## Test the actual invariant

- Prefer real visible controls for UI acceptance. Wait for the target control and then the intended selected/ready object, not any terminal element. Direct imports/adapter calls during startup can race initialization and do not establish user behavior.
- Match terminal output that cannot be confused with command echo. Use unique per-browser markers, explicit shell PID/variable values, and the real backend terminal ID. Labels alone cannot prove process continuity.
- A shell blocked on `read` is an effective oracle for unintended Enter/EOF. Check the marker is absent after the operation, then deliberately press Enter and verify the exact expected value.
- Record browser/backend versions, page errors, failed requests, selected IDs, dimensions, screenshots and command-level exit status. Expected watch aborts or busy-attachment responses should have a narrow, explained classification—not a blanket ignored-error rule.
- If a test fails, capture both browser state and backend/process state before fixture cleanup. Diagnose whether the defect is in the application, the fixture's readiness assumptions, or the environment. Do not replace a failing invariant with a visible-label assertion.

Read [Linux clipboard/audio](references/linux-permissions.md) for permission-sensitive checks, or [isolation and resource faults](references/isolation-and-faults.md) for lifecycle/limits testing. The permission recipe is Linux/X11-specific; it does not establish macOS, Windows, Wayland or physical speaker behavior.

Preserve a ledger that distinguishes implemented, tested, scoped evidence, blocked and untested. Mixed-suite commands can fail overall while some suites genuinely pass. Do not rerun every previously passing suite after a documentation change; rerun relevant tests after implementation changes or unresolved failures, then leave unrelated evidence intact.

Source: Herdr Web `test/{permissions,ui,integration,view_edges,resources,git_failures,watch_resources,ssh,plugin}.py` and `docs/ACCEPTANCE.md`. Historic test counts and exact browser versions are evidence for that project, not requirements for the next one.
