' Launch RLCD bridge with no visible console window.
' Task Scheduler calls this (wscript.exe run-bridge-hidden.vbs) instead of
' the .cmd directly, so the daemon runs fully in the background.

Set fso = CreateObject("Scripting.FileSystemObject")
Set sh  = CreateObject("WScript.Shell")

scriptDir = fso.GetParentFolderName(WScript.ScriptFullName)
sh.CurrentDirectory = fso.BuildPath(fso.GetParentFolderName(scriptDir), "bridge")
sh.Run """" & fso.BuildPath(scriptDir, "run-bridge.cmd") & """", 0, False
