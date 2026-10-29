#ifndef PayloadDir
  #error PayloadDir must name a verified release payload.
#endif
#ifndef ReleaseOutput
  #error ReleaseOutput must name the build output directory.
#endif
#ifndef InstallFileList
  #error InstallFileList must name the generated exact-file manifest.
#endif
#ifndef ReleaseVersion
  #error ReleaseVersion must match the verified payload VERSION.
#endif

[Setup]
AppId={{A1A3AE88-BA31-45CC-98F5-63D6D9804C95}
AppName=ShuJian Cube
AppVersion={#ReleaseVersion}
AppVerName=ShuJian Cube {#ReleaseVersion} Preview
AppPublisher=ShuJian Cube contributors
AppPublisherURL=https://github.com/tianxingstarsky/Super-LLM-distill-Gen
AppSupportURL=https://github.com/tianxingstarsky/Super-LLM-distill-Gen/issues
DefaultDirName={localappdata}\Programs\ShuJian Cube
DefaultGroupName=ShuJian Cube
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0.17763
OutputDir={#ReleaseOutput}
OutputBaseFilename=ShuJian-Cube-{#ReleaseVersion}-Windows-x64-Setup
SetupIconFile={#PayloadDir}\assets\brand\shujian-cube.ico
UninstallDisplayIcon={app}\assets\brand\shujian-cube.ico
Compression=lzma2
SolidCompression=yes
ExtraDiskSpaceRequired=2147483648
WizardStyle=modern
LicenseFile={#PayloadDir}\LICENSE
InfoBeforeFile={#PayloadDir}\scripts\release\INSTALL.txt
CloseApplications=yes
RestartApplications=no
DisableProgramGroupPage=yes
SetupLogging=yes

[Tasks]
Name: "desktopicon"; Description: "Create a desktop shortcut"; GroupDescription: "Shortcuts:"; Flags: unchecked

[Files]
#include InstallFileList

[Icons]
Name: "{group}\ShuJian Cube"; Filename: "{sys}\wscript.exe"; Parameters: """{app}\scripts\start_all.vbs"""; WorkingDir: "{app}"; IconFilename: "{app}\assets\brand\shujian-cube.ico"
Name: "{group}\ShuJian Cube - Browser"; Filename: "{sys}\wscript.exe"; Parameters: """{app}\scripts\release\start_browser.vbs"""; WorkingDir: "{app}"; IconFilename: "{app}\assets\brand\shujian-cube.ico"
Name: "{group}\ShuJian Cube - Repair runtime"; Filename: "{app}\scripts\release\setup_runtime.cmd"; WorkingDir: "{app}"
Name: "{group}\Uninstall ShuJian Cube"; Filename: "{uninstallexe}"
Name: "{autodesktop}\ShuJian Cube"; Filename: "{sys}\wscript.exe"; Parameters: """{app}\scripts\start_all.vbs"""; WorkingDir: "{app}"; IconFilename: "{app}\assets\brand\shujian-cube.ico"; Tasks: desktopicon

[Run]
Filename: "{sys}\wscript.exe"; Parameters: """{app}\scripts\start_all.vbs"""; WorkingDir: "{app}"; Description: "Open ShuJian Cube"; Check: RuntimeReady; Flags: postinstall nowait skipifsilent

[UninstallDelete]
Type: filesandordirs; Name: "{app}\.venv"
Type: filesandordirs; Name: "{app}\.runtime"

[Code]
var
  RuntimeSetupFailed: Boolean;

function FileNeedsUpdate(RelativePath, ExpectedSha256: String): Boolean;
var
  Destination: String;
begin
  Destination := ExpandConstant('{app}') + '\' + RelativePath;
  if FileExists(ExpandConstant('{app}\unins000.exe')) and
     FileExists(ExpandConstant('{app}\unins000.dat')) and FileExists(Destination) then
    Result := CompareText(GetSHA256OfFile(Destination), ExpectedSha256) <> 0
  else
    Result := True;
end;

function RuntimeReady: Boolean;
begin
  Result := not RuntimeSetupFailed;
end;

function GetCustomSetupExitCode: Integer;
begin
  if RuntimeSetupFailed then
    Result := 1
  else
    Result := 0;
end;

function PrepareToInstall(var NeedsRestart: Boolean): String;
var
  AppDir: String;
  ExistingManifest: AnsiString;
  OwnedInstallation: Boolean;
begin
  AppDir := ExpandConstant('{app}');
  OwnedInstallation := LoadStringFromFile(AppDir + '\INSTALLATION.json', ExistingManifest)
    and (Pos('"product_id": "shujian-cube"', String(ExistingManifest)) > 0);
  if DirExists(AppDir + '\.git') or FileExists(AppDir + '\.git') then
    Result := 'Choose a dedicated application folder. A Git checkout cannot be used as the installation folder.'
  else if (DirExists(AppDir + '\.venv') or DirExists(AppDir + '\.runtime')) and not OwnedInstallation then
    Result := 'This folder contains another Python environment. Choose an empty or dedicated ShuJian Cube folder.'
  else
    Result := '';
end;

procedure CurStepChanged(CurStep: TSetupStep);
var
  RuntimeScript: String;
  ExitCode: Integer;
begin
  if CurStep = ssPostInstall then
  begin
    WizardForm.StatusLabel.Caption := 'Preparing application dependencies. Internet access is required.';
    RuntimeScript := ExpandConstant('{app}\scripts\release\setup_runtime.cmd');
    if not Exec(ExpandConstant('{cmd}'), '/D /S /C ""' + RuntimeScript + '""', ExpandConstant('{app}'), SW_HIDE, ewWaitUntilTerminated, ExitCode) then
    begin
      RuntimeSetupFailed := True;
      RaiseException('Runtime setup could not start. Run the Repair runtime shortcut.');
    end;
    if ExitCode <> 0 then
    begin
      RuntimeSetupFailed := True;
      RaiseException('Runtime setup failed. Check data\output\installation.log in the chosen folder, then run setup again.');
    end;
  end;
end;
