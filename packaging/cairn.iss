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
