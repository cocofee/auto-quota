$path='C:\Users\Administrator\Documents\trae_projects\auto-quota\output\tasks\efecd9c5-2327-43e7-8534-d36339969ecf\results.json'
$data=Get-Content -Raw -Path $path | ConvertFrom-Json
$items=@($data.results)
$summary=[ordered]@{}
$summary.total=$items.Count
$summary.light_status=@($items | Group-Object light_status | ForEach-Object { [ordered]@{status=$_.Name; count=$_.Count} })
$summary.final_validation_status=@($items | Group-Object { if($_.final_validation){$_.final_validation.status}else{''} } | ForEach-Object { [ordered]@{status=$_.Name; count=$_.Count} })
$summary.review_focus=@($items | Where-Object { $_.light_status -in @('yellow','red') -or ($_.final_validation -and $_.final_validation.status -ne 'ok') } | ForEach-Object {
    [ordered]@{
        index=$_.bill_item.index
        code=$_.bill_item.code
        bill_name=$_.bill_item.name
        section=$_.bill_item.section
        unit=$_.bill_item.unit
        confidence=$_.confidence_score
        light_status=$_.light_status
        final_status=if($_.final_validation){$_.final_validation.status}else{''}
        issues=if($_.final_validation -and $_.final_validation.issues){ @($_.final_validation.issues | ForEach-Object { ($_.type + ':' + $_.message) }) }else{@()}
        top1_id=if($_.quotas -and $_.quotas.Count -gt 0){$_.quotas[0].quota_id}else{''}
        top1_name=if($_.quotas -and $_.quotas.Count -gt 0){$_.quotas[0].name}else{''}
        alt_ids=if($_.alternatives){ @($_.alternatives | Select-Object -First 3 | ForEach-Object { $_.quota_id }) }else{@()}
        explanation=$_.explanation
    }
})
$summary | ConvertTo-Json -Depth 6
