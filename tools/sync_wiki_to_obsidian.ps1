[CmdletBinding()]
param(
    [string]$SourceWiki = "C:\Users\Administrator\Documents\trae_projects\auto-quota\knowledge_wiki",
    [string]$TargetVault = "",
    [string]$SearchRoot = "D:\Obsidian"
)

$ErrorActionPreference = "Stop"

function Resolve-TargetVault {
    param(
        [string]$ExplicitPath,
        [string]$Root
    )

    if (-not [string]::IsNullOrWhiteSpace($ExplicitPath)) {
        return $ExplicitPath
    }

    $candidates = Get-ChildItem -LiteralPath $Root -Directory -Recurse -Filter "JARVIS-Wiki"
    foreach ($candidate in $candidates) {
        $hasHome = @(Get-ChildItem -LiteralPath $candidate.FullName -Directory | Where-Object { $_.Name -like "00-*" }).Count -gt 0
        $hasRules = @(Get-ChildItem -LiteralPath $candidate.FullName -Directory | Where-Object { $_.Name -like "10-*" }).Count -gt 0
        if ($hasHome -and $hasRules) {
            return $candidate.FullName
        }
    }

    throw "Could not auto-discover JARVIS-Wiki under $Root"
}

function Resolve-NumberedDir {
    param(
        [string]$Root,
        [string]$Prefix
    )

    $match = Get-ChildItem -LiteralPath $Root -Directory | Where-Object { $_.Name -like "$Prefix*" } | Select-Object -First 1
    if (-not $match) {
        throw "Folder with prefix '$Prefix' not found under $Root"
    }
    return $match.FullName
}

$TargetVault = Resolve-TargetVault -ExplicitPath $TargetVault -Root $SearchRoot

$targetMap = @{
    "rules" = "10-"
    "cases" = "20-"
    "methods" = "30-"
    "concepts" = "40-"
    "reviews" = "50-"
    "sources" = "60-"
    "inbox" = "70-"
    "daily" = "80-"
    "entities" = "90-"
}

$manifestFiles = Get-ChildItem -LiteralPath $SourceWiki -Filter ".generated*manifest.json" -File | Sort-Object Name
if (-not $manifestFiles) {
    throw "No generated manifest found under $SourceWiki"
}

$manifestItems = @()
foreach ($manifestFile in $manifestFiles) {
    $manifestItems += (Get-Content -LiteralPath $manifestFile.FullName -Raw -Encoding UTF8 | ConvertFrom-Json)
}
$syncManifestPath = Join-Path $TargetVault ".obsidian_sync_manifest.json"

if (Test-Path -LiteralPath $syncManifestPath) {
    try {
        $oldSync = Get-Content -LiteralPath $syncManifestPath -Raw -Encoding UTF8 | ConvertFrom-Json
        foreach ($item in @($oldSync.files)) {
            $pathText = [string]$item.target_path
            if ([string]::IsNullOrWhiteSpace($pathText)) { continue }
            if ($pathText.StartsWith($TargetVault, [System.StringComparison]::OrdinalIgnoreCase) -and (Test-Path -LiteralPath $pathText)) {
                Remove-Item -LiteralPath $pathText -Force
            }
        }
    } catch {
        Write-Warning "Failed to read previous sync manifest, continuing with current copy."
    }
}

$synced = @()
foreach ($manifest in @($manifestItems)) {
    foreach ($item in @($manifest.files)) {
        $category = [string]$item.category
        $relativePath = [string]$item.relative_path
        if ([string]::IsNullOrWhiteSpace($relativePath)) { continue }
        if (-not $targetMap.ContainsKey($category)) { continue }

        $sourcePath = Join-Path $SourceWiki $relativePath
        if (-not (Test-Path -LiteralPath $sourcePath)) { continue }

        $targetDir = Resolve-NumberedDir -Root $TargetVault -Prefix $targetMap[$category]
        $fileName = [System.IO.Path]::GetFileName($relativePath)
        $targetPath = Join-Path $targetDir $fileName
        Copy-Item -LiteralPath $sourcePath -Destination $targetPath -Force

        $synced += [pscustomobject]@{
            title = [string]$item.title
            category = $category
            target_path = $targetPath
        }
    }
}

$homeDir = Resolve-NumberedDir -Root $TargetVault -Prefix "00-"
$summaryPath = Join-Path $homeDir "04_generated_sync_summary.md"
$generatedAt = [string](Get-Date -Format "s")
$summaryLines = @(
    "---",
    'title: "Generated Sync Summary"',
    'type: "daily_summary"',
    'status: "reviewed"',
    'province: ""',
    'specialty: ""',
    'source_refs:',
    '  - "generated:sync"',
    'source_kind: "system"',
    ('created_at: "{0}"' -f (Get-Date -Format 'yyyy-MM-dd')),
    ('updated_at: "{0}"' -f (Get-Date -Format 'yyyy-MM-dd')),
    'confidence: 100',
    'owner: "codex"',
    'tags:',
    '  - "generated"',
    '  - "sync"',
    '  - "obsidian"',
    'related: []',
    "---",
    "",
    "# Generated Sync Summary",
    "",
    "## Sync",
    ("- source: ``{0}``" -f $SourceWiki),
    ("- target: ``{0}``" -f $TargetVault),
    ("- generated_at: ``{0}``" -f $generatedAt),
    ("- synced_files: ``{0}``" -f (($synced | Measure-Object).Count)),
    "",
    "## Counts"
)

foreach ($group in ($synced | Group-Object category | Sort-Object Name)) {
    $summaryLines += ("- {0}: ``{1}``" -f $group.Name, $group.Count)
}

$summaryLines += ""
$summaryLines += "## Samples"
foreach ($item in ($synced | Select-Object -First 10)) {
    $summaryLines += ("- {0}" -f $item.title)
}
$summaryLines += ""

Set-Content -LiteralPath $summaryPath -Value $summaryLines -Encoding UTF8

$syncManifest = [pscustomobject]@{
    synced_at = Get-Date -Format "s"
    source_manifest = @($manifestFiles | ForEach-Object { $_.FullName })
    files = $synced
}
$syncManifest | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath $syncManifestPath -Encoding UTF8

Write-Output "Synced $($synced.Count) generated wiki files to $TargetVault"
