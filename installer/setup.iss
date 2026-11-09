; Inno Setup script for CK Text Artist.
; Build from the repo root after PyInstaller:   iscc /DAppVersion=1.2.3 installer\setup.iss

#define AppName "CK Text Artist"
#define AppExe "CK Text Artist.exe"
#define RepoUrl "https://github.com/project-ceekay/ck-text-artist"
#ifndef AppVersion
  #define AppVersion "0.0.0"
#endif

[Setup]
; Never change AppId: it is how Windows knows a new version is an upgrade of this program.
AppId={{12290F8E-5960-4E8F-ADC1-DC39088F8D6E}
AppName={#AppName}
AppVersion={#AppVersion}
AppVerName={#AppName} {#AppVersion}
AppPublisher=CK Software
AppPublisherURL={#RepoUrl}
AppSupportURL={#RepoUrl}/issues
AppUpdatesURL={#RepoUrl}/releases
VersionInfoVersion={#AppVersion}
; Per-user install (no admin prompt): %LOCALAPPDATA%\Programs\CK Suite\...
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog
DefaultDirName={autopf}\CK Suite\{#AppName}
DisableProgramGroupPage=yes
DisableDirPage=auto
LicenseFile=..\LICENSE
OutputDir=Output
OutputBaseFilename=CK-Text-Artist-{#AppVersion}-Setup
SetupIconFile=..\favicon.ico
UninstallDisplayIcon={app}\{#AppExe}
UninstallDisplayName={#AppName}
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
CloseApplications=yes

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"

[InstallDelete]
; clear the previous version's bundled libraries so an upgrade never mixes old and new files
Type: filesandordirs; Name: "{app}\_internal"

[Files]
Source: "..\dist\{#AppName}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "..\LICENSE"; DestDir: "{app}"; DestName: "LICENSE.txt"; Flags: ignoreversion
Source: "..\THIRD-PARTY-NOTICES.md"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{autoprograms}\CK Suite\{#AppName}"; Filename: "{app}\{#AppExe}"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#AppExe}"; Tasks: desktopicon

[Run]
; normal install: offer to launch at the end
Filename: "{app}\{#AppExe}"; Description: "{cm:LaunchProgram,{#AppName}}"; Flags: nowait postinstall skipifsilent
; silent install (the program's built-in updater): reopen the program automatically
Filename: "{app}\{#AppExe}"; Flags: nowait; Check: WizardSilent
