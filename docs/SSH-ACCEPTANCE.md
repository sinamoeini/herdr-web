# Separate-host SSH acceptance

`test/ssh_remote.py` tests an existing Herdr Web instance through SSH.
The test requires an explicitly supplied, isolated remote instance.
Without a host, it records `untested` and exits successfully.
The default CI suite tests the same harness against isolated loopback services.
That result does not establish separate-host compatibility.

## Prerequisites

Use a remote test instance with original Herdr 0.9.5 and a compatible Herdr Web executable.
Keep the remote web listener on `127.0.0.1`.
Use an existing trusted SSH alias or `user@host`.
Configure its SSH port and identity outside this test.
The test requires noninteractive authentication and strict host-key acceptance.

Herdr Web disables SSH connection sharing and background startup to retain ownership of each forward.
It never accepts an unknown host key or installs remote services.

Build the local executable with `./build.sh`.
Install OpenSSH on the local test host before the test.
The test does not need local sudo or a local backend.
It starts two private local web processes in diagnostic mode.
One process owns the tested forward.
The other process owns a separate forward for observation and cleanup.

## Run

Replace `test-host` with the SSH alias for the isolated remote test instance.
Replace `9004` with that instance's web port.
The web port is separate from the SSH port.

```sh
python3 test/ssh_remote.py --host test-host --port 9004
```

If you know the remote Herdr Web commit, supply its full SHA with `--remote-source`.
The report labels that value as operator supplied.
The test cannot read the remote executable's build identity through the current API.
It records the remote homepage hash and backend version returned by the service.

## Test scope

The test performs these steps:

1. Open both owned forwards with strict host-key requirements.
2. Fetch the remote homepage and `/api/state`.
3. Create one workspace with a unique test label.
4. Send terminal input only to that workspace's new pane.
5. Record the shell process ID and a test variable.
6. Disconnect the tested forward and require its process and listener to disappear.
7. Reconnect and require the same shell process ID and variable.
8. Stop the tested local web process and require its forward to disappear.
9. Require the remote service, test shell, and preexisting terminal IDs to remain available.
10. Remove the test workspace and both owned forwards.

The separate observation forward permits cleanup after local shutdown or an assertion failure.
If the remote host becomes unreachable, workspace cleanup can fail.
The report records cleanup status rather than claiming success.
Inspect the isolated remote instance before another run after a cleanup failure.
The test never stops the remote web service or backend.
It never types into a preexisting terminal or closes a preexisting workspace.

## Results

The default report is `test-results/ssh-remote/results.json`.
Use `--output` to select another local report path.
Exit code 0 means either a passing test or an explicitly untested run without a host.
Exit code 1 means that a supplied target failed acceptance.
Exit code 2 means that arguments or local prerequisites are invalid.
An unreachable host, unknown host key, or unavailable remote service is a failure when a host was supplied.

The report excludes host names, paths, SSH diagnostics, and terminal output.
It includes the local executable hash, local source revision, backend version, assertions, and cleanup status.
Local process logs remain private under the ignored results directory.
Review those logs before sharing any evidence.
