param(
    [string]$BaseUrl = "http://127.0.0.1:3210",
    [string]$ApiKey = $env:OPENCLAW_API_KEY,
    [string]$TaskId = "",
    [switch]$IncludeOpenApi
)

$ErrorActionPreference = "Stop"

if ([string]::IsNullOrWhiteSpace($ApiKey)) {
    throw "OPENCLAW_API_KEY is required. Pass -ApiKey or set the environment variable."
}

$Headers = @{
    "X-OpenClaw-Key" = $ApiKey
}

function Invoke-BridgeGet {
    param(
        [Parameter(Mandatory = $true)][string]$Path
    )

    $Url = ($BaseUrl.TrimEnd('/')) + $Path
    Write-Host ""
    Write-Host "==> GET $Url" -ForegroundColor Cyan

    try {
        $Response = Invoke-RestMethod -Uri $Url -Headers $Headers -Method Get
        $Response | ConvertTo-Json -Depth 12
    }
    catch {
        Write-Host "Request failed: $Url" -ForegroundColor Red
        if ($_.Exception.Response) {
            $Reader = New-Object System.IO.StreamReader($_.Exception.Response.GetResponseStream())
            $Body = $Reader.ReadToEnd()
            if ($Body) {
                Write-Host $Body -ForegroundColor DarkRed
            }
        }
        throw
    }
}

Write-Host "OpenClaw bridge smoke test" -ForegroundColor Green
Write-Host "BaseUrl: $BaseUrl"

Invoke-BridgeGet -Path "/api/openclaw/health"
Invoke-BridgeGet -Path "/api/openclaw/tasks"

if ($IncludeOpenApi) {
    Invoke-BridgeGet -Path "/api/openclaw/openapi.json"
}

if (-not [string]::IsNullOrWhiteSpace($TaskId)) {
    Invoke-BridgeGet -Path "/api/openclaw/tasks/$TaskId/review-items"
}

Write-Host ""
Write-Host "Done." -ForegroundColor Green
