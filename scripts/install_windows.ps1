param(
  [Parameter(Mandatory = $true)]
  [ValidateNotNullOrEmpty()]
  [string]$MacHost,
  [Parameter(Mandatory = $true)]
  [ValidateNotNullOrEmpty()]
  [string]$MacUser
)
$ErrorActionPreference = 'Stop'
$sourceDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$bridgeSource = Join-Path $sourceDir '..\src\computer_bridge.py'
$installDir = Join-Path $env:USERPROFILE '.codex\computer-bridge'
$binDir = Join-Path $installDir 'bin'
$python = (Get-Command python.exe -ErrorAction Stop).Source
$versionText = & $python --version
if ($versionText -notmatch '^Python (\d+\.\d+)') { throw "Could not read Python version: $versionText" }
$version = [version]$Matches[1]
if ($version -lt [version]'3.11') { throw "Python 3.11+ is required; found $version" }
New-Item -ItemType Directory -Force -Path $installDir, $binDir | Out-Null
$sourceBridge = $bridgeSource
$installedBridge = Join-Path $installDir 'computer_bridge.py'
if ([IO.Path]::GetFullPath($sourceBridge) -ne [IO.Path]::GetFullPath($installedBridge)) {
  Copy-Item -Force $sourceBridge $installedBridge
}
$entry = Join-Path $installDir 'computer_bridge.cmd'
$entryText = "@echo off`r`nset PYTHONUTF8=1`r`n`"$python`" `"$installDir\computer_bridge.py`" %*`r`n"
Set-Content -Encoding ascii $entry $entryText

$archive = Join-Path $env:TEMP 'winappcli-x64-v0.7.1.zip'
$sourceArchive = Join-Path $sourceDir 'winappcli-x64-v0.7.1.zip'
if (Test-Path $sourceArchive) {
  Copy-Item -Force $sourceArchive $archive
} else {
  $release = Invoke-RestMethod 'https://api.github.com/repos/microsoft/winappCli/releases/tags/v0.7.1' -Headers @{ 'User-Agent' = 'Codex-Computer-Bridge' }
  $asset = $release.assets | Where-Object name -eq 'winappcli-x64.zip' | Select-Object -First 1
  if (-not $asset) { throw 'The pinned WinApp CLI v0.7.1 x64 archive was not found.' }
  Invoke-WebRequest -Uri $asset.browser_download_url -OutFile $archive
}
$expectedHash = 'd925d1e32cdc320b6d271f653fd2cd3c05b5d7558deb20d1b548bead77900fcf'
$actualHash = (Get-FileHash -Algorithm SHA256 $archive).Hash.ToLowerInvariant()
if ($actualHash -ne $expectedHash) { throw "WinApp CLI v0.7.1 archive SHA-256 mismatch: $actualHash" }
$expanded = Join-Path $installDir 'winapp-v0.7.1'
if (Test-Path $expanded) { Remove-Item -Recurse -Force $expanded }
Expand-Archive -Force $archive $expanded
$winapp = Get-ChildItem -Path $expanded -Filter 'winapp.exe' -Recurse | Select-Object -First 1
if (-not $winapp) { throw 'winapp.exe was not present in the official archive.' }
Copy-Item -Force $winapp.FullName (Join-Path $binDir 'winapp.exe')
Remove-Item -Force $archive

$alias = 'mac-worker'
$sshConfig = Join-Path $env:USERPROFILE '.ssh\config'
$hasAlias = (Test-Path $sshConfig) -and ([IO.File]::ReadAllText($sshConfig) -match '(?im)^Host\s+mac-worker(?:\s|$)')
if (-not $hasAlias) {
  $macAddress = $MacHost
  New-Item -ItemType Directory -Force (Split-Path $sshConfig) | Out-Null
  Add-Content -Encoding ascii $sshConfig "`nHost $alias`n  HostName $macAddress`n  User $macUser`n  IdentitiesOnly yes`n  IdentityFile ~/.ssh/codex_computer_bridge`n  StrictHostKeyChecking yes`n"
}

$identity = Join-Path $env:USERPROFILE '.ssh\codex_computer_bridge'
$publicKey = "$identity.pub"
if (-not (Test-Path $identity)) {
  $keygenArguments = @('-q', '-t', 'ed25519', '-N', '""', '-C', 'codex-computer-bridge-windows', '-f', $identity)
  $keygen = Start-Process -FilePath (Get-Command ssh-keygen.exe).Source -ArgumentList $keygenArguments -NoNewWindow -Wait -PassThru
  if ($keygen.ExitCode -ne 0) { throw 'ssh-keygen failed.' }
}
if (-not (Test-Path $publicKey)) { throw "Public key missing: $publicKey" }
$currentUser = (& whoami.exe).Trim()
$sshConfig = Join-Path $env:USERPROFILE '.ssh\config'
foreach ($protectedPath in @($sshConfig, $identity)) {
  & icacls.exe $protectedPath /inheritance:r /grant:r "${currentUser}:F" 'SYSTEM:F' | Out-Null
  if ($LASTEXITCODE -ne 0) { throw "Could not secure OpenSSH permissions on $protectedPath" }
}
$publicKeyValue = (Get-Content -Raw $publicKey).Trim()
$hostKey = Join-Path $env:USERPROFILE '.ssh\known_hosts'
if (Test-Path $hostKey) {
  $null = & ssh-keygen.exe -F $MacHost -f $hostKey 2>$null
  if ($LASTEXITCODE -ne 0) { Write-Warning "Mac host key not pinned. Add its verified SSH fingerprint to $hostKey before using mac-worker." }
} else {
  Write-Warning "Mac host key not pinned. Create $hostKey with the verified SSH host key before using mac-worker."
}
Write-Output "Windows public key (add this line to the Mac user's ~/.ssh/authorized_keys): $publicKeyValue"

$codex = Get-Command codex.cmd, codex.exe, codex -ErrorAction SilentlyContinue | Select-Object -First 1
if (-not $codex) { throw 'Codex CLI was not found. Install it before enabling the MCP server.' }
$pythonExe = $python
$userPath = [Environment]::GetEnvironmentVariable('Path', 'User')
if (($userPath -split ';') -notcontains $installDir) {
  [Environment]::SetEnvironmentVariable('Path', (($userPath.TrimEnd(';') + ';' + $installDir).TrimStart(';')), 'User')
}
$env:Path = "$installDir;$env:Path"
$taskName = 'Codex Computer Bridge Worker'
$action = New-ScheduledTaskAction -Execute $pythonExe -Argument ('"{0}" worker' -f (Join-Path $installDir 'computer_bridge.py')) -WorkingDirectory $installDir
$trigger = New-ScheduledTaskTrigger -AtLogOn -User $currentUser
$principal = New-ScheduledTaskPrincipal -UserId $currentUser -LogonType Interactive -RunLevel Limited
$settings = New-ScheduledTaskSettingsSet -ExecutionTimeLimit ([TimeSpan]::Zero) -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1)
Register-ScheduledTask -TaskName $taskName -Action $action -Trigger $trigger -Principal $principal -Settings $settings -Force | Out-Null
Start-ScheduledTask -TaskName $taskName

& $codex.Source mcp remove computer-bridge 2>$null
& $codex.Source mcp add --env PYTHONUTF8=1 computer-bridge -- $pythonExe (Join-Path $installDir 'computer_bridge.py') mcp --peer mac=mac-worker
if ($LASTEXITCODE -ne 0) { throw 'Could not register computer-bridge in Codex.' }
& $pythonExe (Join-Path $installDir 'computer_bridge.py') capabilities
Write-Output 'Installed. Restart Codex to load computer-bridge.'
