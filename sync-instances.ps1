param(
  [Parameter(Mandatory = $true)]
  [string[]] $Targets,

  [string] $RemoteBoardPath = "~/.codex/instance-board",

  [ValidateSet("push", "pull", "both")]
  [string] $Direction = "both",

  [switch] $SkipLocalRefresh,
  [switch] $SkipRemoteRefresh,
  [switch] $DryRun
)

$ErrorActionPreference = "Stop"
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path

function Run {
  param([Parameter(Mandatory = $true)] [string[]] $Command)
  if ($DryRun) {
    Write-Host ($Command -join " ")
    return
  }
  & $Command[0] @($Command | Select-Object -Skip 1)
}

function Ensure-RemoteBoardDirs {
  param([Parameter(Mandatory = $true)] [string] $Target)
  $cmd = "powershell -NoProfile -ExecutionPolicy Bypass -Command `"New-Item -ItemType Directory -Force -Path '$RemoteBoardPath/machines','$RemoteBoardPath/sessions'`""
  Run @("ssh", $Target, $cmd)
}

New-Item -ItemType Directory -Force -Path "$ScriptDir\machines", "$ScriptDir\sessions" | Out-Null

if (-not $SkipLocalRefresh) {
  Run @("python", (Join-Path $ScriptDir "codex_board.py"), "refresh", "--quiet")
}

foreach ($Target in $Targets) {
  Ensure-RemoteBoardDirs -Target $Target
  if (-not $SkipRemoteRefresh) {
    Run @("ssh", $Target, "powershell -NoProfile -ExecutionPolicy Bypass -Command codex-dash refresh --quiet")
  }

  if ($Direction -in @("pull", "both")) {
    Run @("scp", "${Target}:$RemoteBoardPath/machines/*.json", "$ScriptDir\machines\")
    Run @("scp", "${Target}:$RemoteBoardPath/sessions/*.json", "$ScriptDir\sessions\")
  }

  if ($Direction -in @("push", "both")) {
    if (-not $SkipLocalRefresh) {
      Run @("python", (Join-Path $ScriptDir "codex_board.py"), "refresh", "--quiet")
    }
    Run @("scp", "$ScriptDir\machines\*.json", "${Target}:$RemoteBoardPath/machines/")
    Run @("scp", "$ScriptDir\sessions\*.json", "${Target}:$RemoteBoardPath/sessions/")
  }

  Write-Host "Synced Codex instance state with $Target"
}
