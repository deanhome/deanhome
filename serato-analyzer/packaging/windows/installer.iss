; Inno Setup 6 script for the optional Windows installer.
; Invoked by build_windows.ps1 -Installer, which passes AppVersion, AppArch and SourceDir.

#ifndef AppVersion
  #define AppVersion "0.1.0"
#endif
#ifndef AppArch
  #define AppArch "x64"
#endif
#ifndef SourceDir
  #define SourceDir "..\..\dist\windows-x64\SeratoAnalyzer"
#endif

[Setup]
AppId={{6C3E2B1A-8F4D-4C55-9B1E-2D7A5E0C9F31}
AppName=Serato Analyzer
AppVersion={#AppVersion}
AppPublisher=deanhome
DefaultDirName={autopf}\Serato Analyzer
DefaultGroupName=Serato Analyzer
DisableProgramGroupPage=yes
; Per-user install by default; no administrator rights needed.
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog
OutputDir=..\..\dist
OutputBaseFilename=SeratoAnalyzer-{#AppVersion}-windows-{#AppArch}-setup
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
MinVersion=10.0
#if AppArch == "arm64"
ArchitecturesAllowed=arm64
ArchitecturesInstallIn64BitMode=arm64
#else
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
#endif
UninstallDisplayIcon={app}\SeratoAnalyzer.exe

[Files]
Source: "{#SourceDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\Serato Analyzer"; Filename: "{app}\SeratoAnalyzer.exe"
Name: "{autodesktop}\Serato Analyzer"; Filename: "{app}\SeratoAnalyzer.exe"; Tasks: desktopicon

[Tasks]
Name: "desktopicon"; Description: "Create a desktop shortcut"; Flags: unchecked

[Run]
Filename: "{app}\SeratoAnalyzer.exe"; Description: "Launch Serato Analyzer"; Flags: nowait postinstall skipifsilent

; Settings, the database, backups and the artwork cache live in %APPDATA% and
; %LOCALAPPDATA%\SeratoAnalyzer and are intentionally kept on uninstall.
