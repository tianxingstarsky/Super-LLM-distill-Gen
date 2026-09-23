Option Explicit
Dim shell, fs, root, python, script
Set shell = CreateObject("WScript.Shell")
Set fs = CreateObject("Scripting.FileSystemObject")
root = fs.GetParentFolderName(fs.GetParentFolderName(WScript.ScriptFullName))
python = root & "\.venv\Scripts\pythonw.exe"
script = root & "\scripts\launch_console.py"
If Not fs.FileExists(python) Then
    MsgBox "ShuJian Cube Python environment is missing:" & vbCrLf & python & vbCrLf & vbCrLf & _
           "Run these commands in the project folder, then double-click again:" & vbCrLf & _
           "python -m venv .venv" & vbCrLf & _
           ".venv\Scripts\python.exe -m pip install -r requirements.txt", vbCritical, "ShuJian Cube startup"
    WScript.Quit 1
End If
If Not fs.FileExists(script) Then
    MsgBox "ShuJian Cube launcher is missing:" & vbCrLf & script & vbCrLf & _
           "Please restore the project files.", vbCritical, "ShuJian Cube startup"
    WScript.Quit 1
End If
shell.CurrentDirectory = root
On Error Resume Next
shell.Run Chr(34) & python & Chr(34) & " " & Chr(34) & script & Chr(34), 0, False
If Err.Number <> 0 Then
    MsgBox "Could not start ShuJian Cube:" & vbCrLf & Err.Description & vbCrLf & _
           "See README.md for setup steps.", vbCritical, "ShuJian Cube startup"
    WScript.Quit 1
End If
