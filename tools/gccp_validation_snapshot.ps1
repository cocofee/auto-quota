param(
    [Parameter(Mandatory = $true)]
    [ValidateSet("before", "after", "compare")]
    [string]$Mode,

    [Parameter(Mandatory = $true)]
    [string]$Name,

    [string]$ProjectPath = "",

    [switch]$SkipLocalFiles,

    [string]$OutDir = "C:\Users\Administrator\Documents\trae_projects\auto-quota\reports\gccp_validation"
)

$ErrorActionPreference = "Stop"

function Resolve-SnapshotPath {
    param([string]$Suffix)
    New-Item -ItemType Directory -Force -Path $OutDir | Out-Null
    return (Join-Path $OutDir "$Name.$Suffix.json")
}

function Get-TargetFiles {
    $roots = @(
        "$env:APPDATA\Glodon\GCCP7",
        "$env:APPDATA\Glodon\GCCP6",
        "$env:USERPROFILE\Documents\Glodon",
        "$env:PROGRAMDATA\GrandSoft",
        "$env:PROGRAMDATA\Grandsoft Shared"
    )

    $include = @(
        "*.GSP", "*.gsp", "*.GSF", "*.gsf", "*.RGF", "*.rgf",
        "*.log", "*.xml", "*.json", "*.ini", "*.db", "*.sqlite",
        "*.GBQ6", "*.gbq6", "*.GBQ7", "*.gbq7", "*.GPB7", "*.gpb7",
        "*.dat"
    )

    foreach ($root in $roots) {
        if (-not (Test-Path -LiteralPath $root)) { continue }
        Get-ChildItem -LiteralPath $root -Recurse -File -ErrorAction SilentlyContinue -Include $include |
            Where-Object {
                $_.FullName -match "GCCP|Glodon|GrandSoft|Archive|Reuse|Cloud|Temp|WorkCopy|RecoverBack|UpdateBak|MatchRule|GSPFiles|ssFiles"
            }
    }
}

function Get-FileSnapshot {
    Get-TargetFiles | ForEach-Object {
        $hash = $null
        try {
            if ($_.Length -le 200MB) {
                $hash = (Get-FileHash -LiteralPath $_.FullName -Algorithm SHA256).Hash
            }
        } catch {
            $hash = "HASH_ERROR"
        }

        [pscustomobject]@{
            FullName = $_.FullName
            Length = $_.Length
            LastWriteTimeUtc = $_.LastWriteTimeUtc.ToString("o")
            Hash = $hash
        }
    } | Sort-Object FullName
}

function Get-ZipEntrySnapshot {
    param([string]$Path)

    if ([string]::IsNullOrWhiteSpace($Path) -or -not (Test-Path -LiteralPath $Path)) {
        return @()
    }

    Add-Type -AssemblyName System.IO.Compression.FileSystem
    $zip = [System.IO.Compression.ZipFile]::OpenRead($Path)
    try {
        return @($zip.Entries | ForEach-Object {
            [pscustomobject]@{
                FullName = $_.FullName
                Length = $_.Length
                CompressedLength = $_.CompressedLength
                LastWriteTime = $_.LastWriteTime.ToString("o")
            }
        } | Sort-Object FullName)
    } finally {
        $zip.Dispose()
    }
}

function New-Snapshot {
    $files = @()
    if (-not $SkipLocalFiles) {
        $files = @(Get-FileSnapshot)
    }

    [pscustomobject]@{
        Name = $Name
        CreatedAt = (Get-Date).ToUniversalTime().ToString("o")
        ProjectPath = $ProjectPath
        Files = $files
        ProjectEntries = @(Get-ZipEntrySnapshot -Path $ProjectPath)
    }
}

function Read-Snapshot {
    param([string]$Path)
    Get-Content -LiteralPath $Path -Encoding UTF8 -Raw | ConvertFrom-Json
}

function Compare-Items {
    param(
        [object[]]$Before,
        [object[]]$After,
        [string]$Key
    )

    $beforeMap = @{}
    foreach ($item in $Before) { $beforeMap[$item.$Key] = $item }

    $afterMap = @{}
    foreach ($item in $After) { $afterMap[$item.$Key] = $item }

    $added = @()
    $removed = @()
    $changed = @()

    foreach ($keyValue in $afterMap.Keys) {
        if (-not $beforeMap.ContainsKey($keyValue)) {
            $added += $afterMap[$keyValue]
            continue
        }
        $b = $beforeMap[$keyValue] | ConvertTo-Json -Compress
        $a = $afterMap[$keyValue] | ConvertTo-Json -Compress
        if ($b -ne $a) {
            $changed += [pscustomobject]@{
                Key = $keyValue
                Before = $beforeMap[$keyValue]
                After = $afterMap[$keyValue]
            }
        }
    }

    foreach ($keyValue in $beforeMap.Keys) {
        if (-not $afterMap.ContainsKey($keyValue)) {
            $removed += $beforeMap[$keyValue]
        }
    }

    [pscustomobject]@{
        Added = $added
        Removed = $removed
        Changed = $changed
    }
}

function Get-Count {
    param([object]$Items)
    if ($null -eq $Items) { return 0 }
    return @($Items).Count
}

function Write-MarkdownSummary {
    param(
        [object]$Report,
        [string]$Path
    )

    $fileAdded = Get-Count $Report.FileDiff.Added
    $fileRemoved = Get-Count $Report.FileDiff.Removed
    $fileChanged = Get-Count $Report.FileDiff.Changed
    $entryAdded = Get-Count $Report.ProjectEntryDiff.Added
    $entryRemoved = Get-Count $Report.ProjectEntryDiff.Removed
    $entryChanged = Get-Count $Report.ProjectEntryDiff.Changed

    $warnings = New-Object System.Collections.Generic.List[string]
    if ($entryAdded -gt 0 -or $entryRemoved -gt 0 -or $entryChanged -gt 0) {
        $warnings.Add("Project package changed. This is expected after GCCP pricing reuse, but bill identity fields must still be checked through GCCP export/report comparison.")
    }
    if ($fileAdded -gt 0 -or $fileRemoved -gt 0 -or $fileChanged -gt 0) {
        $warnings.Add("GCCP/Glodon local files changed. Review the JSON diff if you are trying to prove a local archive/cache write path.")
    }
    if ($warnings.Count -eq 0) {
        $warnings.Add("No file/package changes detected by this snapshot scope.")
    }

    $lines = New-Object System.Collections.Generic.List[string]
    $lines.Add("# GCCP Validation Diff")
    $lines.Add("")
    $lines.Add("- Name: $($Report.Name)")
    $lines.Add("- ComparedAt: $($Report.ComparedAt)")
    $lines.Add("")
    $lines.Add("## Counts")
    $lines.Add("")
    $lines.Add("| Scope | Added | Removed | Changed |")
    $lines.Add("| --- | ---: | ---: | ---: |")
    $lines.Add("| Local GCCP files | $fileAdded | $fileRemoved | $fileChanged |")
    $lines.Add("| Project package entries | $entryAdded | $entryRemoved | $entryChanged |")
    $lines.Add("")
    $lines.Add("## Warnings")
    $lines.Add("")
    foreach ($warning in $warnings) {
        $lines.Add("- $warning")
    }
    $lines.Add("")
    $lines.Add("## Formal project rule")
    $lines.Add("")
    $lines.Add("AutoQuota must not write the formal GBQ7 project. Formal project changes should come only from GCCP reuse pricing, and bill identity fields must remain unchanged.")

    $lines | Set-Content -LiteralPath $Path -Encoding UTF8
}

if ($Mode -eq "before" -or $Mode -eq "after") {
    $snapshot = New-Snapshot
    $path = Resolve-SnapshotPath -Suffix $Mode
    $snapshot | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath $path -Encoding UTF8
    Write-Host "Snapshot saved: $path"
    Write-Host "Files: $($snapshot.Files.Count); Project entries: $($snapshot.ProjectEntries.Count)"
    exit 0
}

$beforePath = Resolve-SnapshotPath -Suffix "before"
$afterPath = Resolve-SnapshotPath -Suffix "after"
if (-not (Test-Path -LiteralPath $beforePath)) { throw "Missing before snapshot: $beforePath" }
if (-not (Test-Path -LiteralPath $afterPath)) { throw "Missing after snapshot: $afterPath" }

$before = Read-Snapshot -Path $beforePath
$after = Read-Snapshot -Path $afterPath

$report = [pscustomobject]@{
    Name = $Name
    ComparedAt = (Get-Date).ToUniversalTime().ToString("o")
    FileDiff = Compare-Items -Before @($before.Files) -After @($after.Files) -Key "FullName"
    ProjectEntryDiff = Compare-Items -Before @($before.ProjectEntries) -After @($after.ProjectEntries) -Key "FullName"
}

$reportPath = Resolve-SnapshotPath -Suffix "diff"
$report | ConvertTo-Json -Depth 12 | Set-Content -LiteralPath $reportPath -Encoding UTF8
$summaryPath = Join-Path $OutDir "$Name.diff.md"
Write-MarkdownSummary -Report $report -Path $summaryPath

Write-Host "Diff saved: $reportPath"
Write-Host "Summary saved: $summaryPath"
Write-Host "Files added: $($report.FileDiff.Added.Count)"
Write-Host "Files removed: $($report.FileDiff.Removed.Count)"
Write-Host "Files changed: $($report.FileDiff.Changed.Count)"
Write-Host "Project entries added: $($report.ProjectEntryDiff.Added.Count)"
Write-Host "Project entries removed: $($report.ProjectEntryDiff.Removed.Count)"
Write-Host "Project entries changed: $($report.ProjectEntryDiff.Changed.Count)"
