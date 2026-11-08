#define AppVersion "0.1.0"

[Setup]
AppId={{7E2A9C14-4B6F-4E3A-9C21-8F5D1A0B6E33}
AppName=Cairn
AppVersion={#AppVersion}
AppPublisher=Cairn
DefaultDirName={localappdata}\Cairn
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
OutputDir=out
OutputBaseFilename=CairnSetup
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
CloseApplications=yes
RestartApplications=no
AppMutex=CairnSingleton
UninstallDisplayName=Cairn

[Files]
Source: "stage\Cairn\*"; DestDir: "{app}"; Flags: recursesubdirs ignoreversion

[Icons]
Name: "{autoprograms}\Cairn"; Filename: "{app}\pythonw.exe"; Parameters: "-m shakya"; WorkingDir: "{app}"
Name: "{autodesktop}\Cairn"; Filename: "{app}\pythonw.exe"; Parameters: "-m shakya"; WorkingDir: "{app}"

[Run]
Filename: "{app}\pythonw.exe"; Parameters: "-m shakya"; WorkingDir: "{app}"; Flags: nowait postinstall
