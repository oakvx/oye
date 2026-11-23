#define AppName "Oye"
#define AppVersion "44.2"
#define AppExe "OyeStudio.exe"

[Setup]
AppId={{D8AF462E-7B48-4770-AABE-1E5F89D35621}
AppName={#AppName}
AppVersion={#AppVersion}
AppPublisher=Oye
DefaultDirName={localappdata}\Programs\Oye
DefaultGroupName=Oye
DisableProgramGroupPage=yes
UninstallDisplayName=Oye
OutputDir=..\release
OutputBaseFilename=Oye-v44-Setup-Windows-x64
Compression=lzma2
SolidCompression=yes
PrivilegesRequired=lowest
WizardStyle=modern
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
CloseApplications=yes
RestartApplications=no
UsePreviousAppDir=yes

[Languages]
Name: "italian"; MessagesFile: "compiler:Languages\Italian.isl"

[Tasks]
Name: "desktopicon"; Description: "Crea un collegamento sul desktop"; GroupDescription: "Collegamenti:"; Flags: unchecked

[Files]
Source: "..\dist\OyeStudio\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{autoprograms}\Oye"; Filename: "{app}\{#AppExe}"; WorkingDir: "{app}"
Name: "{autodesktop}\Oye"; Filename: "{app}\{#AppExe}"; WorkingDir: "{app}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#AppExe}"; Description: "Avvia Oye"; Flags: nowait postinstall skipifsilent

[Code]
procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
var
  DataPath: String;
begin
  { Unattended uninstall always preserves user data. No destructive dialog. }
  if UninstallSilent() then
    Exit;
  if CurUninstallStep = usPostUninstall then
  begin
    DataPath := ExpandConstant('{localappdata}\Oye');
    if DirExists(DataPath) then
    begin
      if MsgBox('Vuoi eliminare anche le calibrazioni, le impostazioni e i dati salvati da Oye?' + #13#10 +
         'Scegli NO per conservarli e riutilizzarli in una futura installazione.',
         mbConfirmation, MB_YESNO or MB_DEFBUTTON2) = IDYES then
        DelTree(DataPath, True, True, True);
    end;
  end;
end;
