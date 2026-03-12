' 后台启动本地匹配服务（无窗口）
' 用于Windows任务计划程序开机自启
Set WshShell = CreateObject("WScript.Shell")
WshShell.CurrentDirectory = "C:\Users\Administrator\Documents\trae_projects\auto-quota"
WshShell.Run "pythonw local_match_server.py", 0, False
