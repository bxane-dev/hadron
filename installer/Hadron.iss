#define MyAppName "Hadron"
#define MyAppVersion "3.0.0"
#define MyAppPublisher "bxane (bxane-dev)"
#define MyAppExeName "Hadron.exe"

[Setup]
AppId={{F9E5EC7C-56CD-4E43-B2AB-2B0D94239DB2}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
DefaultDirName={localappdata}\Programs\Hadron
DefaultGroupName=Hadron
DisableProgramGroupPage=yes
OutputDir=..\release
OutputBaseFilename=Hadron-Setup-v{#MyAppVersion}
SetupIconFile=..\assets\hadron.ico
UninstallDisplayIcon={app}\{#MyAppExeName}
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
CloseApplications=yes
RestartApplications=no
AppMutex=HadronDesktopAppMutex
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "Create a desktop shortcut"; GroupDescription: "Additional shortcuts:"; Flags: unchecked

[Files]
Source: "..\dist\Hadron.exe"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\dist\HadronBatch.exe"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\dist\HadronJobs.exe"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\dist\HadronDoctor.exe"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\dist\HadronVerify.exe"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\dist\HadronSign.exe"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\dist\HadronCapsule.exe"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\dist\HadronReproduce.exe"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\dist\HadronRegression.exe"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\dist\HadronCampaign.exe"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\dist\HadronCampaignRun.exe"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\dist\HadronPipeline.exe"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\dist\HadronRecovery.exe"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\dist\HadronUpdater.exe"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\release-public.pem"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\ENABLE_PORTABLE_MODE.txt"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\example_jobs.json"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{autoprograms}\Hadron"; Filename: "{app}\Hadron.exe"; WorkingDir: "{app}"
Name: "{autoprograms}\Hadron Doctor"; Filename: "{app}\HadronDoctor.exe"; WorkingDir: "{app}"
Name: "{autoprograms}\Hadron Recovery"; Filename: "{app}\HadronRecovery.exe"; WorkingDir: "{app}"
Name: "{autoprograms}\Hadron Check for Updates"; Filename: "{app}\HadronUpdater.exe"; Parameters: "check"; WorkingDir: "{app}"
Name: "{autodesktop}\Hadron"; Filename: "{app}\Hadron.exe"; WorkingDir: "{app}"; Tasks: desktopicon

[Run]
Filename: "{app}\Hadron.exe"; Description: "Launch Hadron"; Flags: nowait postinstall skipifsilent
