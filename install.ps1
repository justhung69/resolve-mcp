<#
.SYNOPSIS
  One-step installer for the DaVinci Resolve MCP server (Windows).

.DESCRIPTION
  1. Checks the Resolve version (warns about 21.1+ Free, where in-app Python is gone).
  2. Creates .venv next to this script and installs the `mcp` package (asks first unless -Yes).
  3. Copies ClaudeBridge.py / ClaudeBridge_Stop.py into Resolve's user Scripts\Utility folder and
     bridge_core.py into %LOCALAPPDATA%\ResolveMCP.
  4. Registers the server in every Claude Desktop config it finds (classic and Microsoft Store builds),
     after writing a timestamped backup. Existing servers and settings are kept.
  5. Prints the `claude mcp add` command for Claude Code.

.EXAMPLE
  powershell -ExecutionPolicy Bypass -File .\install.ps1
  powershell -ExecutionPolicy Bypass -File .\install.ps1 -Yes -SkipDesktop
#>
param(
    [switch]$Yes,           # do not ask before pip install / config changes
    [switch]$SkipDesktop,   # do not touch Claude Desktop configs
    [string]$Python = ""    # explicit python.exe (3.10+)
)
$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
$ServerName = "davinci-resolve"

function Ask($q) {
    if ($Yes) { return $true }
    $a = Read-Host "$q [y/N]"
    return $a -match '^(y|yes|c|co)$'
}
function Write-JsonNoBom($path, $obj) {
    $json = $obj | ConvertTo-Json -Depth 50
    [IO.File]::WriteAllText($path, $json, (New-Object Text.UTF8Encoding($false)))
}

Write-Host "== DaVinci Resolve MCP installer ==" -ForegroundColor Cyan

# 1. Resolve version ------------------------------------------------------------
$resolveExe = "C:\Program Files\Blackmagic Design\DaVinci Resolve\Resolve.exe"
if (Test-Path $resolveExe) {
    $ver = (Get-Item $resolveExe).VersionInfo.ProductVersion
    Write-Host "Resolve $ver found."
    $v = [version]($ver -replace '[^\d\.]', '')
    if ($v -ge [version]"21.1") {
        Write-Warning ("Resolve $ver detected. Resolve 21.1+ FREE no longer runs Python scripts from the Scripts menu, " +
            "so the bridge cannot start (Studio is fine). On Free, use 21.0.x - see README.md.")
    }
} else {
    Write-Warning "Resolve.exe not found at the default location - continuing anyway."
}

# 2. Python + venv ----------------------------------------------------------------
if (-not $Python) {
    $cands = @()
    $py = Get-Command py -ErrorAction SilentlyContinue
    if ($py) { try { $cands += (& py -3 -c "import sys;print(sys.executable)") } catch {} }
    $cands += Get-ChildItem "$env:LOCALAPPDATA\Programs\Python\Python3*\python.exe", "$env:LOCALAPPDATA\Python\pythoncore-3*\python.exe" -ErrorAction SilentlyContinue | ForEach-Object FullName
    $Python = $cands | Where-Object { $_ -and (Test-Path $_) -and ($_ -notmatch 'WindowsApps') } | Select-Object -First 1
}
if (-not $Python) { throw "No Python 3.10+ found. Install Python from python.org (64-bit) and re-run, or pass -Python <path>." }
$pv = & $Python -c "import sys;print('%d.%d'%sys.version_info[:2])"
if ([version]$pv -lt [version]"3.10") { throw "Python $pv is too old (need 3.10+): $Python" }
Write-Host "Python $pv : $Python"
Write-Host "Note: Resolve runs ClaudeBridge.py with ITS OWN choice of system Python (the one registered for all users or on PATH)."

$venvPy = Join-Path $Root ".venv\Scripts\python.exe"
if (-not (Test-Path $venvPy)) {
    & $Python -m venv (Join-Path $Root ".venv")
}
$hasMcp = & $venvPy -c "import importlib.util;print(bool(importlib.util.find_spec('mcp')))"
if ($hasMcp -ne "True") {
    if (Ask "Install the Python package 'mcp' (official MCP SDK, from PyPI) into $Root\.venv?") {
        & $venvPy -m pip install --disable-pip-version-check -q -r (Join-Path $Root "requirements.txt")
    } else { throw "The 'mcp' package is required." }
}
Write-Host "MCP SDK: $(& $venvPy -c "import importlib.metadata as m;print(m.version('mcp'))")"

# 3. Bridge files -------------------------------------------------------------------
$scripts = Join-Path $env:APPDATA "Blackmagic Design\DaVinci Resolve\Support\Fusion\Scripts\Utility"
$state = Join-Path $env:LOCALAPPDATA "ResolveMCP"
New-Item -ItemType Directory -Force $scripts, $state | Out-Null
Copy-Item (Join-Path $Root "bridge\ClaudeBridge.py"), (Join-Path $Root "bridge\ClaudeBridge_Stop.py") $scripts -Force
Copy-Item (Join-Path $Root "bridge\bridge_core.py") $state -Force
$cfg = Join-Path $state "config.json"
if (-not (Test-Path $cfg)) { Write-JsonNoBom $cfg ([ordered]@{ allow_exec = $false }) }
Write-Host "Bridge installed: $scripts\ClaudeBridge.py (+ core in $state)"

# 4. Claude Desktop -------------------------------------------------------------------
$serverPy = Join-Path $Root "server\mcp_server.py"
$entry = [ordered]@{ command = $venvPy; args = @($serverPy) }
if (-not $SkipDesktop) {
    $configs = @(Join-Path $env:APPDATA "Claude\claude_desktop_config.json")
    $configs += Get-ChildItem "$env:LOCALAPPDATA\Packages" -Directory -Filter "Claude_*" -ErrorAction SilentlyContinue |
        ForEach-Object { Join-Path $_.FullName "LocalCache\Roaming\Claude\claude_desktop_config.json" }
    $configs = $configs | Where-Object { Test-Path (Split-Path $_) } | Select-Object -Unique
    if (-not $configs) { Write-Host "Claude Desktop not found - skipping." }
    foreach ($c in $configs) {
        if (-not (Ask "Register '$ServerName' in $c (a backup is written first)?")) { continue }
        $obj = [ordered]@{}
        if (Test-Path $c) {
            $backup = "$c.bak-$(Get-Date -Format yyyyMMdd-HHmmss)"
            Copy-Item $c $backup
            Write-Host "  backup: $backup"
            $raw = Get-Content $c -Raw
            if ($raw.Trim()) { $obj = $raw | ConvertFrom-Json }
        }
        if (-not ($obj.PSObject.Properties.Name -contains "mcpServers")) {
            $obj | Add-Member -NotePropertyName mcpServers -NotePropertyValue ([pscustomobject]@{}) -Force
        }
        if ($obj.mcpServers.PSObject.Properties.Name -contains $ServerName) {
            $obj.mcpServers.$ServerName = [pscustomobject]$entry
        } else {
            $obj.mcpServers | Add-Member -NotePropertyName $ServerName -NotePropertyValue ([pscustomobject]$entry)
        }
        Write-JsonNoBom $c $obj
        Write-Host "  registered in $c"
    }
    Write-Host "Restart Claude Desktop to load the server."
}

# 5. Claude Code ------------------------------------------------------------------------
Write-Host ""
Write-Host "For Claude Code, run:" -ForegroundColor Cyan
Write-Host "  claude mcp add $ServerName --scope user -- `"$venvPy`" `"$serverPy`""
Write-Host ""
Write-Host "Every Resolve session: open a project, then Workspace > Scripts > ClaudeBridge." -ForegroundColor Green
