param(
    [string]$SourceDir = $PSScriptRoot,
    [string]$TargetDir = "$env:USERPROFILE\.openclaw\workspace\skills\auto-quota-watcher",
    [switch]$DryRun,
    [switch]$Backup
)

$ErrorActionPreference = 'Stop'

$filesToCopy = @(
    'SKILL.md',
    'AGENT_SOURCE_LEARNING_TEMPLATE.md',
    'scripts\auto_match.py',
    'scripts\config.json'
)

if (-not (Test-Path $SourceDir)) {
    throw "SourceDir not found: $SourceDir"
}

if (-not (Test-Path $TargetDir)) {
    New-Item -ItemType Directory -Path $TargetDir -Force | Out-Null
}

$timestamp = Get-Date -Format 'yyyyMMdd-HHmmss'
$copied = @()

foreach ($relative in $filesToCopy) {
    $src = Join-Path $SourceDir $relative
    if (-not (Test-Path $src)) {
        throw "Missing source file: $src"
    }

    $dst = Join-Path $TargetDir $relative
    $dstDir = Split-Path -Parent $dst
    if (-not (Test-Path $dstDir) -and -not $DryRun) {
        New-Item -ItemType Directory -Path $dstDir -Force | Out-Null
    }

    if ($Backup -and (Test-Path $dst) -and -not $DryRun) {
        Copy-Item -LiteralPath $dst -Destination "$dst.bak.$timestamp" -Force
    }

    if (-not $DryRun) {
        Copy-Item -LiteralPath $src -Destination $dst -Force
    }
    $copied += $relative
}

Write-Host 'OpenClaw skill sync complete.'
Write-Host "Source: $SourceDir"
Write-Host "Target: $TargetDir"
Write-Host 'Files:'
$copied | ForEach-Object { Write-Host "- $_" }
