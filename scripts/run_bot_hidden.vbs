' Reviewed manual/logon launcher. Starts supervisor; NEVER resume/enable live.
Option Explicit
Dim fso, shell, root, python, terminalScript, command, code
Set fso = CreateObject("Scripting.FileSystemObject")
Set shell = CreateObject("WScript.Shell")
root = fso.GetParentFolderName(fso.GetParentFolderName(WScript.ScriptFullName))
If InStr(root, Chr(34)) > 0 Then WScript.Quit 2
If Not fso.FileExists(fso.BuildPath(root, ".env")) Then WScript.Quit 2
' The persistent operator stop sentinel is never silently deleted at logon.
If fso.FileExists(fso.BuildPath(root, "data\runtime\operator-stop.json")) Then WScript.Quit 0
python = fso.BuildPath(root, ".venv\Scripts\pythonw.exe")
If Not fso.FileExists(python) Then WScript.Quit 2
terminalScript = fso.BuildPath(root, "scripts\run_mt5_background.vbs")
code = shell.Run("wscript.exe //B //Nologo " & Chr(34) & terminalScript & Chr(34), 0, True)
If code <> 0 Then WScript.Quit code
shell.CurrentDirectory = root
command = Chr(34) & python & Chr(34) & " " & Chr(34) & fso.BuildPath(root, "watchdog.py") & Chr(34) & _
          " --env-file " & Chr(34) & fso.BuildPath(root, ".env") & Chr(34)
' Wait for supervisor so Task Scheduler can detect running instance; window hidden.
code = shell.Run(command, 0, True)
WScript.Quit code
