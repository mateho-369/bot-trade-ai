' Reviewed manual/logon launcher. Starts supervisor; NEVER resume/enable live.
Option Explicit
Dim fso, shell, root, python, terminalScript, command, code
Set fso = CreateObject("Scripting.FileSystemObject")
Set shell = CreateObject("WScript.Shell")
root = fso.GetParentFolderName(fso.GetParentFolderName(WScript.ScriptFullName))
If InStr(root, Chr(34)) > 0 Then WScript.Quit 2
If Not fso.FileExists(fso.BuildPath(root, ".env")) Then WScript.Quit 2
' Respect a true persistent stop request; an explicit false marker is preserved.
python = fso.BuildPath(root, ".venv\Scripts\python.exe")
If Not fso.FileExists(python) Then WScript.Quit 2
shell.CurrentDirectory = root
command = Chr(34) & python & Chr(34) & " -c " & Chr(34) & _
          "import sys; from core.settings import Settings; from app.process_guard import operator_stop_requested; sys.exit(3 if operator_stop_requested(Settings(_env_file='.env')) else 0)" & Chr(34)
code = shell.Run(command, 0, True)
If code <> 0 Then WScript.Quit code
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
