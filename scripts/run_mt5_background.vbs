' Explicit operator launcher. Minimized window is not a Windows service.
Option Explicit
Dim fso, shell, root, envPath, envFile, line, key, value, terminal, backend
Set fso = CreateObject("Scripting.FileSystemObject")
Set shell = CreateObject("WScript.Shell")
root = fso.GetParentFolderName(fso.GetParentFolderName(WScript.ScriptFullName))
envPath = fso.BuildPath(root, ".env")
If Not fso.FileExists(envPath) Then WScript.Quit 2
terminal = "C:/Program Files/MetaTrader 5/terminal64.exe"
backend = "mock"
Set envFile = fso.OpenTextFile(envPath, 1, False)
Do Until envFile.AtEndOfStream
    line = Trim(envFile.ReadLine)
    If Len(line) > 0 And Left(line, 1) <> "#" And InStr(line, "=") > 0 Then
        key = UCase(Trim(Left(line, InStr(line, "=") - 1)))
        value = Trim(Mid(line, InStr(line, "=") + 1))
        If Len(value) >= 2 Then
            If (Left(value, 1) = Chr(34) And Right(value, 1) = Chr(34)) Or _
               (Left(value, 1) = "'" And Right(value, 1) = "'") Then value = Mid(value, 2, Len(value) - 2)
        End If
        If key = "MT5_TERMINAL_PATH" Then terminal = value
        If key = "MT5_BACKEND" Then backend = LCase(value)
    End If
Loop
envFile.Close
' Mock mode never opens a native terminal. Review simple literal dotenv paths.
If backend <> "real" Then WScript.Quit 0
If InStr(terminal, Chr(34)) > 0 Or InStr(terminal, vbCr) > 0 Or InStr(terminal, vbLf) > 0 Then WScript.Quit 2
If LCase(fso.GetExtensionName(terminal)) <> "exe" Or Not fso.FileExists(terminal) Then WScript.Quit 2
' Window style 2 = minimized; no passwords or login arguments are passed.
shell.Run Chr(34) & terminal & Chr(34), 2, False
