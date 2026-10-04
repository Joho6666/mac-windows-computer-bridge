# Security and public-repository hygiene

## Trust model

- Keep the network private to a tailnet. Never port-forward the worker, RPC spool, or MCP stdio transport to the public internet.
- Use dedicated Ed25519 keys, restrictive file ACLs, and verified SSH host fingerprints. Do not use `StrictHostKeyChecking no`.
- Treat both Mac and Windows MCP clients as trusted operators: Python jobs execute with the current user's permissions.
- Do not use this prototype with untrusted callers or sensitive files without adding authentication, authorization, auditing, request validation, and retention controls.
- Desktop control is high impact. Require explicit target-window validation, use test copies, and pause automation during human remote-control sessions.

## Never publish

- Private SSH keys, personal public keys, `authorized_keys`, `known_hosts`, live SSH configs, tailnet IPs, MagicDNS names, local usernames, computer hostnames, and screenshots containing real data.
- `.env` files, API tokens, job databases, job logs, RPC spool files, task arguments, generated output files, test datasets, or browser/app profiles.
- Downloaded vendor archives or executables. The installer fetches the pinned WinApp CLI release and verifies its hash.

The `.gitignore` excludes common local artifacts, but it is not a substitute for review. Run a secret scanner and inspect the complete staged diff before publishing. If a credential is ever committed, revoke/rotate it immediately; deleting the file in a later commit is not sufficient.

## Public visibility and licensing

This repository is public for process sharing, but no open-source license has been selected. Do not add a license without the maintainer's decision.
