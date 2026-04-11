$node=(Get-Command node.exe).Source
& $node 'C:\Users\Administrator\Documents\trae_projects\auto-quota\tmp_task_summary.js' | Out-File -FilePath 'C:\Users\Administrator\Documents\trae_projects\auto-quota\tmp_task_summary_output.json' -Encoding utf8
