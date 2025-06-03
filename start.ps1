# start.ps1 — placed in the root of your project

# 1) Figure out where this script actually lives (resolve symlinks)
$scriptPath = $MyInvocation.MyCommand.Path
$scriptItem = Get-Item -LiteralPath $scriptPath -Force
if ($scriptItem.LinkType) {
    $resolved = Resolve-Path -LiteralPath $scriptItem.Target
    $scriptPath = $resolved.Path
} else {
    $scriptPath = (Resolve-Path -LiteralPath $scriptPath).Path
}
$scriptDir = Split-Path -LiteralPath $scriptPath -Parent

# 2) Jump into the project root
Set-Location $scriptDir

# 3) Activate the venv next to it
& "$scriptDir\.venv\Scripts\Activate.ps1"

# 4) Delegate to your CLI, forwarding all args
& python -m main @args
