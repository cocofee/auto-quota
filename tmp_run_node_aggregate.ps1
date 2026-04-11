$node=(Get-Command node.exe).Source
& $node 'C:\Users\Administrator\Documents\trae_projects\auto-quota\tmp_task_aggregate.js' | Out-File -FilePath 'C:\Users\Administrator\Documents\trae_projects\auto-quota\tmp_task_aggregate_output.json' -Encoding utf8
