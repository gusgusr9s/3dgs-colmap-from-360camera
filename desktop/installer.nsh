; Updates and uninstall preserve the workspace and Electron userData.
; Electron's updater installs only after the authenticated backend drains.
!macro StudioWaitForExit processName
  StrCpy $R1 0
  ${Do}
    ${nsProcess::FindProcess} "${processName}" $R0
    ${If} $R0 != 0
      ${ExitDo}
    ${EndIf}
    ${If} $R1 >= 10
      IfSilent +2
      MessageBox MB_OK|MB_ICONEXCLAMATION "360 Studio is still working. Finish jobs and close the app safely before installing."
      SetErrorLevel 2
      Quit
    ${EndIf}
    Sleep 500
    IntOp $R1 $R1 + 1
  ${Loop}
!macroend

!macro customCheckAppRunning
  !insertmacro StudioWaitForExit "${APP_EXECUTABLE_FILENAME}"
  !insertmacro StudioWaitForExit "studio360-backend.exe"
!macroend

!macro customInstall
  DetailPrint "Sources, Jobs and Models are stored outside this installation."
!macroend
