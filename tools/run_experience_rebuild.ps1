param(
    [string]$LogPath = ""
)

$ErrorActionPreference = "Stop"

$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

if ([string]::IsNullOrWhiteSpace($LogPath)) {
    $stamp = Get-Date -Format "yyyyMMdd_HHmmss"
    $LogPath = Join-Path $root "logs\experience_rebuild_$stamp.all.log"
}

"[$(Get-Date -Format s)] rebuild-start" | Tee-Object -FilePath $LogPath -Append

try {
    python tools\rebuild_index_qwen3.py --exp-only 2>&1 | Tee-Object -FilePath $LogPath -Append
    "[$(Get-Date -Format s)] rebuild-end success" | Tee-Object -FilePath $LogPath -Append
} catch {
    "[$(Get-Date -Format s)] rebuild-end failed: $($_.Exception.Message)" | Tee-Object -FilePath $LogPath -Append
    throw
}
