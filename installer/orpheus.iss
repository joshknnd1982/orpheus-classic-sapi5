; Inno Setup script for the Orpheus Classic SAPI5 voices.
; Installs the complete Orpheus Classic engine (all nine languages and all
; voice data), the 32-bit and 64-bit SAPI5 interfaces, the engine host and
; the configuration utility.  The wizard uses only standard pages, which are
; screen-reader accessible, and writes a detailed setup log.

#define MyAppName "Orpheus Classic SAPI5"
#define MyAppVersion "1.0.0"
#define MyAppPublisher "Orpheus Classic SAPI5 Project"
#ifndef SourceDir
  #define SourceDir "..\output"
#endif

[Setup]
AppId={{3ad50c0e-a85e-4682-a26a-18f9576d067c}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
DefaultDirName={autopf}\OrpheusClassicSAPI
DefaultGroupName=Orpheus Classic
DisableProgramGroupPage=yes
ArchitecturesInstallIn64BitMode=x64compatible
PrivilegesRequired=admin
OutputBaseFilename=OrpheusClassicSAPI_Setup
OutputDir=.
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
SetupLogging=yes
UninstallDisplayIcon={app}\OrpheusClassicConfig.exe
UninstallDisplayName={#MyAppName}

[Files]
Source: "{#SourceDir}\OrpheusClassicSAPI.dll"; DestDir: "{app}"; Flags: ignoreversion regserver 32bit
Source: "{#SourceDir}\x64\OrpheusClassicSAPI.dll"; DestDir: "{app}\x64"; Flags: ignoreversion regserver 64bit; Check: Is64BitInstallMode
Source: "{#SourceDir}\orpheus-classic-host.exe"; DestDir: "{app}"; Flags: ignoreversion
Source: "{#SourceDir}\OrpheusClassicConfig.exe"; DestDir: "{app}"; Flags: ignoreversion
Source: "{#SourceDir}\orpheus\*"; DestDir: "{app}\orpheus"; Flags: ignoreversion recursesubdirs

[Icons]
Name: "{group}\Orpheus Classic Configuration"; Filename: "{app}\OrpheusClassicConfig.exe"
Name: "{autodesktop}\Orpheus Classic Configuration"; Filename: "{app}\OrpheusClassicConfig.exe"

[Run]
Filename: "{app}\OrpheusClassicConfig.exe"; Description: "Open the Orpheus Classic configuration utility"; Flags: postinstall nowait skipifsilent

[UninstallRun]
Filename: "{sys}\taskkill.exe"; Parameters: "/f /im orpheus-classic-host.exe"; RunOnceId: "StopOrpheusHost"; Flags: runhidden waituntilterminated

[UninstallDelete]
Type: filesandordirs; Name: "{app}\logs"

[Code]
// Stop a running engine host so files can be replaced on upgrade.  The host
// is stateless; SAPI clients respawn it automatically.
function PrepareToInstall(var NeedsRestart: Boolean): String;
var
  ResultCode: Integer;
begin
  Result := '';
  Exec(ExpandConstant('{sys}\taskkill.exe'), '/f /im orpheus-classic-host.exe', '',
       SW_HIDE, ewWaitUntilTerminated, ResultCode);
end;

// Preserve the setup log for debugging: copy it into the application folder.
procedure CurStepChanged(CurStep: TSetupStep);
var
  LogDir: String;
begin
  if CurStep = ssDone then
  begin
    LogDir := ExpandConstant('{app}\logs');
    if not DirExists(LogDir) then
      CreateDir(LogDir);
    CopyFile(ExpandConstant('{log}'), LogDir + '\install.log', False);
  end;
end;
