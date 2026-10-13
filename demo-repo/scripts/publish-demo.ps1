<#
.SYNOPSIS
  Builds the pr-gate-demo git repository from demo-repo/ and pushes main, vuln-pr and hostile-pr.

.EXAMPLE
  powershell -ExecutionPolicy Bypass -File demo-repo\scripts\publish-demo.ps1 -RepoUrl https://github.com/<you>/pr-gate-demo.git

  The GitHub repository must already exist and be EMPTY. No pull requests are opened:
  open them yourself only after the webhook is registered (see SETUP.md).
#>
param(
  [Parameter(Mandatory = $true)][string]$RepoUrl,
  [string]$Target = ""
)
$ErrorActionPreference = "Stop"
$demo = Split-Path -Parent $PSScriptRoot
if (-not $Target) {
  # Default: a pr-gate-demo folder next to the cloned pr-gate repository.
  $Target = Join-Path (Split-Path -Parent (Split-Path -Parent $demo)) "pr-gate-demo"
}

function Invoke-Git {
  git @args
  if ($LASTEXITCODE -ne 0) { throw "git $($args -join ' ') failed" }
}

function Copy-Overlay([string]$From) {
  # -Force includes dot-files/dot-folders such as .claude and .semgrepignore
  Get-ChildItem -Force -LiteralPath $From | Copy-Item -Destination $Target -Recurse -Force
}

if (Test-Path $Target) { throw "$Target already exists. Delete it or pass -Target <new folder>." }
New-Item -ItemType Directory -Path $Target | Out-Null
Copy-Overlay (Join-Path $demo "base")
Set-Location $Target

Invoke-Git init -q -b main
Invoke-Git add -A
Invoke-Git commit -q -m "Initial clean demo app"
Invoke-Git remote add origin $RepoUrl
Invoke-Git push -u origin main

Invoke-Git checkout -q -b vuln-pr
Copy-Overlay (Join-Path $demo "prs\vuln")
Invoke-Git add -A
Invoke-Git commit -q -m "Add user search, admin import and payment client"
Invoke-Git push -u origin vuln-pr

Invoke-Git checkout -q main
Invoke-Git checkout -q -b hostile-pr
Copy-Overlay (Join-Path $demo "prs\hostile")
Invoke-Git add -A
Invoke-Git commit -q -m "Add calculator helper and project settings"
Write-Host "Files in the hostile commit:"
Invoke-Git show --stat --oneline HEAD
Invoke-Git push -u origin hostile-pr

Invoke-Git checkout -q main
Write-Host ""
Write-Host "Done. Branches pushed: main, vuln-pr, hostile-pr. Do NOT open PRs until the webhook is registered."
