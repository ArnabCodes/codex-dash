param(
  [string] $BoardHome = (Split-Path -Parent $MyInvocation.MyCommand.Path),
  [string] $CommandDir = (Join-Path $env:APPDATA "npm"),
  [string] $MachineId = "",
  [switch] $SetBoardHome,
  [switch] $AddToPath
)

$ErrorActionPreference = "Stop"
$BoardHome = (Resolve-Path -LiteralPath $BoardHome).Path
$Script = Join-Path $BoardHome "codex_board.py"

if (-not (Test-Path -LiteralPath $Script)) {
  throw "codex_board.py not found in $BoardHome"
}

$Python = Get-Command python -ErrorAction SilentlyContinue
if (-not $Python) {
  throw "python was not found on PATH. Install Python or add it to PATH first."
}

New-Item -ItemType Directory -Force -Path $CommandDir | Out-Null

$Ps1 = Join-Path $CommandDir "codex-dash.ps1"
$Cmd = Join-Path $CommandDir "codex-dash.cmd"

@"
`$ErrorActionPreference = "Stop"
`$Script = "$Script"
python "`$Script" @args
"@ | Set-Content -LiteralPath $Ps1 -Encoding UTF8

@"
@echo off
python "$Script" %*
"@ | Set-Content -LiteralPath $Cmd -Encoding ASCII

if ($SetBoardHome) {
  [Environment]::SetEnvironmentVariable("CODEX_BOARD_HOME", $BoardHome, "User")
  $env:CODEX_BOARD_HOME = $BoardHome
}

if ($MachineId) {
  [Environment]::SetEnvironmentVariable("CODEX_BOARD_MACHINE_ID", $MachineId, "User")
  $env:CODEX_BOARD_MACHINE_ID = $MachineId
}

if ($AddToPath) {
  $UserPath = [Environment]::GetEnvironmentVariable("Path", "User")
  $Entries = @($UserPath -split ";" | Where-Object { $_ })
  if ($Entries -notcontains $CommandDir) {
    $NewPath = (@($Entries + $CommandDir) -join ";")
    [Environment]::SetEnvironmentVariable("Path", $NewPath, "User")
    $env:Path = "$env:Path;$CommandDir"
  }
}

Write-Host "Installed codex-dash:"
Write-Host "  $Ps1"
Write-Host "  $Cmd"
Write-Host ""
Write-Host "Try it in a new terminal:"
Write-Host "  codex-dash"
Write-Host "  codex-dash keys"
