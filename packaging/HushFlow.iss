#define MyAppName "HushFlow"
#define MyAppVersion "2.1.0"
#define MyAppPublisher "HushFlow"
#define MyAppURL "https://github.com/Sabarixx/HushFlow"
#define MyAppExeName "HushFlow.exe"

[Setup]
AppId={{B4A83F2D-7F12-4A9E-9C35-8D11E90A4B21}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppVerName={#MyAppName} {#MyAppVersion}
AppPublisher={#MyAppPublisher}
AppPublisherURL={#MyAppURL}
AppSupportURL={#MyAppURL}/issues
AppUpdatesURL={#MyAppURL}/releases
DefaultDirName={localappdata}\Programs\{#MyAppName}
DefaultGroupName={#MyAppName}
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
OutputDir=..\dist
OutputBaseFilename=HushFlow-Setup-{#MyAppVersion}
SetupIconFile=..\assets\hushflow.ico
UninstallDisplayIcon={app}\{#MyAppExeName}
UninstallDisplayName={#MyAppName}
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
CloseApplications=yes
RestartApplications=no

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"

[Files]
Source: "..\dist\HushFlow\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{autoprograms}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon

[InstallDelete]
; Clean up legacy prototype shortcuts from prior dev setup
Type: files; Name: "{userdesktop}\WhisperFlow Studio.lnk"
Type: files; Name: "{userdesktop}\WhisperFlow.lnk"
Type: files; Name: "{userdesktop}\Start WhisperFlow (Local AI).lnk"
Type: files; Name: "{userdesktop}\Stop WhisperFlow.lnk"
Type: files; Name: "{userstartup}\WhisperFlow.lnk"

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "{cm:LaunchProgram,{#StringChange(MyAppName, '&', '&&')}}"; Flags: nowait postinstall skipifsilent

[UninstallRun]
Filename: "{app}\{#MyAppExeName}"; Parameters: "--stop"; Flags: runhidden waituntilterminated; RunOnceId: "StopHushFlowEngine"
