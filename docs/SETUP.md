# Setup: Mac and Windows

## 1. Prepare both computers

- Install Tailscale, sign in to the same tailnet, and record each device's tailnet DNS name or stable tailnet IP privately.
- Install Python 3.11 or newer on both computers.
- On the Mac, enable **System Settings → General → Sharing → Remote Login** for the account that runs the worker.
- On Windows, enable the OpenSSH Server optional feature from an elevated PowerShell window if it is not already present:

  ```powershell
  Add-WindowsCapability -Online -Name OpenSSH.Server~~~~0.0.1.0
  Start-Service sshd
  Set-Service -Name sshd -StartupType Automatic
  Get-Service sshd
  ```

  See Microsoft's [OpenSSH Server setup guide](https://learn.microsoft.com/en-us/windows-server/administration/openssh/openssh_install_firstuse) for current Windows-version details.

- Verify SSH reachability over the tailnet. Restrict Windows inbound SSH to the tailnet/private network and the intended user/device; do not expose TCP 22 to the public internet.
- Generate a dedicated Ed25519 key pair for each direction. Keep private keys on their source computer; only copy the corresponding public key to the destination user's `authorized_keys`.
- Verify and pin each SSH server's host key fingerprint through a trusted channel. Keep `StrictHostKeyChecking yes`.
- Configure the Mac-to-Windows alias in the Mac user's `~/.ssh/config` (use your real, privately stored host/user/key values):

  ```sshconfig
  Host windows-worker
    HostName <windows-tailnet-name-or-ip>
    User <windows-user>
    IdentityFile ~/.ssh/codex_mac_to_windows
    IdentitiesOnly yes
    StrictHostKeyChecking yes
  ```

- Add the Mac public key to the standard Windows user's `~/.ssh/authorized_keys` and apply OpenSSH-compatible ACLs. The Windows installer creates the reverse Windows-to-Mac key and the `mac-worker` alias.
- Test both aliases with `ssh -o BatchMode=yes <alias> <read-only-command>` before installing the bridge.

Do not copy example IPs, usernames, or host fingerprints from another machine. Avoid reusing a personal SSH key if a dedicated bridge key is practical.

## 2. Install on Windows first

Open a normal PowerShell window as the Windows user who will run the worker. Do not run as Administrator unless a specific prerequisite requires it.

```powershell
Set-ExecutionPolicy -Scope Process Bypass
cd <path-to-repository>
.\scripts\install_windows.ps1 -MacHost <verified-mac-tailnet-host> -MacUser <mac-account-name>
```

The installer checks Python, installs the pinned WinApp CLI release, creates the `mac-worker` SSH alias if missing, creates a dedicated key if needed, registers **Codex Computer Bridge Worker** to run at user logon, starts it, and registers the local MCP entry. It prints the Windows public key; add that public key to the Mac user's `~/.ssh/authorized_keys` and set restrictive permissions. Do not publish the key or the actual SSH configuration.

Before enabling the reverse path, add the Mac server's verified host key to Windows `known_hosts`. The installer warns if the key is not pinned and keeps strict checking enabled.

## 3. Install on Mac

After Windows is reachable from the Mac and its public key is present on the Mac, run:

```sh
cd <path-to-repository>
COMPUTER_BRIDGE_WINDOWS_ALIAS=windows-worker zsh scripts/install_macos.sh
```

If the preferred Python is not the first `python3` on PATH, set `COMPUTER_BRIDGE_PYTHON` to a Python 3.11+ executable. The installer creates a per-user LaunchAgent, a command shim, and the Mac MCP registration. It reads the Windows public key from `~/.ssh/codex_computer_bridge.pub` via the configured SSH alias and adds it to `authorized_keys` only if missing.

## 4. Load and smoke-test MCP

Restart the AI client on both computers. Confirm the `computer-bridge` server appears and can report `computer.capabilities` for `local` and the configured peer. Submit a harmless Python job that prints a fixed string, then verify the execution host and job status. Only after this passes, test a uniquely named file under the bridge's managed data directory.

## Network direction and trust

Each AI client talks to a local stdio MCP process. The local MCP process reaches the peer over SSH/SCP and asks its worker to execute a bounded operation. The worker does not bind a public TCP listener. Tailscale reachability alone is not authorization; SSH keys and host-key validation are also required.
