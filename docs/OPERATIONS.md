# Operations and MCP tools

The MCP server exposes:

| Tool | Purpose |
|---|---|
| `computer_capabilities(target)` | Report runtime, host platform, registered operations, and desktop adapters. |
| `computer_submit(target, request_id, operation, arguments)` | Submit an idempotent `system.info`, file, Python, or desktop job. |
| `computer_job_status(target, job_id)` | Retrieve queue state and result. |
| `computer_job_cancel(target, job_id)` | Cancel queued work or request active subprocess termination. Verify the process stopped; cancellation cannot roll back completed application changes. |
| `computer_file_put(target, ...)` | Send one 256 KiB chunk; repeat using one upload ID and final SHA-256. |
| `computer_file_get(target, ...)` | Retrieve a chunk and verify the complete reconstructed file hash. |

Targets are `local` or a configured peer alias. File paths are constrained to the bridge's managed file root. Python jobs run under a per-job working directory. Use a unique `request_id` for each logical operation and reuse the same ID only for an exact retry. If a client times out, query job status before deciding whether to retry.

## Safe smoke tests

1. Query capabilities locally and remotely; verify host, platform, Python version, and interactive-session state.
2. Submit `print('bridge smoke test')` to the peer and check the returned host and exit code.
3. Transfer a tiny uniquely named UTF-8 file into the managed test directory and verify its SHA-256 in both directions.
4. Only after the first three pass, test a dedicated Notepad/TextEdit window. Save only within a newly created test folder.
5. Test large files, Office flows, cancellation, and worker restart as separate cases with logs and cleanup.

## Data layout

The default root is `~/.computer-bridge` on each machine. It contains the SQLite queue, per-job working directories, managed files, and Windows-to-Mac RPC spool directories. Use `COMPUTER_BRIDGE_HOME` to relocate the root before starting either process. Back up only the data you intend to retain; job arguments and logs may contain user-provided content.

## Limits

- One worker processes jobs serially per computer; long work can delay later jobs.
- File transfers are limited to 256 MiB per file and sent in 256 KiB chunks.
- The current download path re-reads and hashes the file for requested chunks; very large downloads may be slower than direct SCP.
- Desktop actions share the real user's focus and input devices. Coordinate with the person at the computer.
- A capability listing shows detected executables and OS, not that an application workflow has been accepted end-to-end.
