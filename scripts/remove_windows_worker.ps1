$ErrorActionPreference = 'Stop'
Unregister-ScheduledTask -TaskName 'Codex Computer Bridge Worker' -Confirm:$false -ErrorAction SilentlyContinue
codex mcp remove computer-bridge
Write-Output 'Removed the worker task and Codex MCP registration. Bridge data was preserved.'
