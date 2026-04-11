$node=(Get-Command node.exe).Source
& $node 'C:\Users\Administrator\Documents\trae_projects\auto-quota\tmp_task_report.js' | Out-File -FilePath 'C:\Users\Administrator\Documents\trae_projects\auto-quota\tmp_task_report.txt' -Encoding utf8
