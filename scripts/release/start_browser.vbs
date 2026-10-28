Option Explicit
Dim shell, fs, root, python, launcher
Set shell = CreateObject("WScript.Shell")
Set fs = CreateObject("Scripting.FileSystemObject")
root = fs.GetParentFolderName(fs.GetParentFolderName(fs.GetParentFolderName(WScript.ScriptFullName)))
python = root & "\.venv\Scripts\pythonw.exe"
launcher = root & "\scripts\launch_console.py"
If Not fs.FileExists(python) Then
    MsgBox "The local runtime is not ready. Run the Repair runtime shortcut, then try again.", vbCritical, "ShuJian Cube"
    WScript.Quit 1
End If
shell.CurrentDirectory = root
shell.Run Chr(34) & python & Chr(34) & " " & Chr(34) & launcher & Chr(34), 0, False
