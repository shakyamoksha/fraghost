#define AppVersion "0.1.0"

[Setup]
AppId={{7E2A9C14-4B6F-4E3A-9C21-8F5D1A0B6E33}
AppName=fraghost
AppVersion={#AppVersion}
AppPublisher=fraghost
DefaultDirName={localappdata}\fraghost
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
OutputDir=out
OutputBaseFilename=fraghost-setup
SetupIconFile=..\shakya\assets\fraghost.ico
UninstallDisplayIcon={app}\shakya\assets\fraghost.ico
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
CloseApplications=yes
RestartApplications=no
AppMutex=fraghostSingleton,CairnSingleton
UninstallDisplayName=fraghost

[Files]
Source: "stage\fraghost\*"; DestDir: "{app}"; Flags: recursesubdirs ignoreversion

[Icons]
Name: "{autoprograms}\fraghost"; Filename: "{app}\pythonw.exe"; Parameters: "-m shakya"; WorkingDir: "{app}"; IconFilename: "{app}\shakya\assets\fraghost.ico"
Name: "{autodesktop}\fraghost"; Filename: "{app}\pythonw.exe"; Parameters: "-m shakya"; WorkingDir: "{app}"; IconFilename: "{app}\shakya\assets\fraghost.ico"

[Run]
Filename: "{app}\pythonw.exe"; Parameters: "-m shakya"; WorkingDir: "{app}"; Flags: nowait postinstall

[Code]
function FirewallCommand(const Params: String): Boolean;
var
  ResultCode: Integer;
begin
  if IsAdminInstallMode then
    Result := Exec(ExpandConstant('{sys}\netsh.exe'), Params, '', SW_HIDE, ewWaitUntilTerminated, ResultCode)
  else
    Result := ShellExec('runas', ExpandConstant('{sys}\netsh.exe'), Params, '', SW_HIDE, ewWaitUntilTerminated, ResultCode);
end;

procedure AddFirewallRule;
var
  ProgramPath, Saved: String;
begin
  ProgramPath := ExpandConstant('{app}\pythonw.exe');
  if RegQueryStringValue(HKCU, 'Software\fraghost', 'FirewallProgram', Saved) and (Saved = ProgramPath) then
    Exit;
  { One administrator prompt at install, the same moment BitTorrent writes its firewall exception. }
  if not FirewallCommand('advfirewall firewall add rule name="fraghost" dir=in action=allow program="' + ProgramPath + '" enable=yes profile=private,public,domain') then
    Exit;
  RegWriteStringValue(HKCU, 'Software\fraghost', 'FirewallProgram', ProgramPath);
end;

procedure CurStepChanged(CurStep: TSetupStep);
begin
  if CurStep = ssPostInstall then
    AddFirewallRule;
end;

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
begin
  if CurUninstallStep = usUninstall then
  begin
    FirewallCommand('advfirewall firewall delete rule name="fraghost"');
    RegDeleteValue(HKCU, 'Software\fraghost', 'FirewallProgram');
    DeleteFile(ExpandConstant('{userappdata}\Shakya\ghost-endpoints.json'));
  end;
end;
