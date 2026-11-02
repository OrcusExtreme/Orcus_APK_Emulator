; Orcus APK Emulator: per-user installer (ARCHITECTURE.md §11.8).
;
; tools/build.py --installer compiles it with ISCC (Inno Setup 6):
;   /DAppVersion=<version> /DNumericVersion=<a.b.c.d> /DSourceDir=<the PyInstaller app folder> /DIconFile=<icon.ico>
; No administrator rights (everything under the user's own keys and the chosen folder). Not code-signed. It carries
; no part of the Android SDK: the setup wizard downloads that on each PC after a person agrees to Google's license.
;
; One folder is chosen, the Orcus folder (default D:\OrcusAPK, C:\OrcusAPK without a fixed D: drive): the program
; goes into its app\ folder and app\orcus_home.txt points the program at the Orcus folder (paths.resolve_home).
; Uninstalling asks whether the virtual phone, the SDK, logs and settings in the Orcus folder go too (default: they
; stay, and a new install goes on with them). Only Orcus's own subfolders are deleted, never the folder's other files.
; Silent use for a lab: Setup /VERYSILENT /HOME=E:\Orcus, unins000.exe /VERYSILENT /DELETEDATA=1.

#ifndef AppVersion
  #define AppVersion "0.0.0"
#endif
#ifndef NumericVersion
  #define NumericVersion "0.0.0.0"
#endif
#ifndef SourceDir
  #error Pass /DSourceDir=<the PyInstaller app folder>
#endif

[Setup]
AppId={{6C1D3F8A-4E2B-4B7C-9A51-2D8E7F0A3B64}
AppName=Orcus APK Emulator
AppVersion={#AppVersion}
AppVerName=Orcus APK Emulator {#AppVersion}
AppPublisher=OrcusExtreme
VersionInfoVersion={#NumericVersion}
VersionInfoProductName=Orcus APK Emulator
VersionInfoDescription=Orcus APK Emulator setup
DefaultDirName={code:DefaultAppDir}
DisableDirPage=yes
DisableProgramGroupPage=yes
UsePreviousAppDir=yes
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0.19041
AppMutex=Local\OrcusAPK.Instance
ChangesAssociations=yes
OutputBaseFilename=OrcusAPK-Setup-{#AppVersion}
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
ShowLanguageDialog=auto
#ifdef IconFile
SetupIconFile={#IconFile}
#endif
UninstallDisplayIcon={app}\OrcusAPK.exe
UninstallDisplayName=Orcus APK Emulator

[Languages]
Name: "korean"; MessagesFile: "compiler:Languages\Korean.isl"
Name: "english"; MessagesFile: "compiler:Default.isl"

[CustomMessages]
korean.HomeCaption=Orcus 폴더
korean.HomeDescription=가상 휴대폰과 Android SDK를 둘 폴더를 고르세요.
korean.HomeSub=이 폴더에 프로그램(app 폴더)과, 처음 실행할 때 내려받는 Android SDK와 가상 휴대폰이 들어갑니다(설치 뒤 약 14 GB, 내려받는 동안에는 24 GB). 영문·숫자로 된 40자 이하 경로여야 하고, Google Drive가 동기화하는 폴더는 피하세요.
korean.BadHome=드라이브 문자로 시작하는 영문·숫자 경로를 40자 이하로 쓰세요(예: D:\OrcusAPK).%n드라이브 바로 아래(예: D:\)나 이동식·네트워크 드라이브는 쓸 수 없습니다.
korean.AssocApk=.apk 파일의 '연결 프로그램'에 Orcus APK Emulator 추가
korean.ApkFileType=Android 앱 (APK)
korean.DeleteDataQ=%1 폴더에 남은 가상 휴대폰, 내려받은 Android SDK, 로그와 설정(약 14 GB)도 지울까요?%n%n[아니요]: 남겨 둡니다. 다시 설치하면 그대로 이어서 씁니다.%n[예]: 지웁니다. 다시 설치하면 SDK를 다시 내려받고 라이선스에 다시 동의해야 합니다.
korean.DeleteFailed=일부를 지우지 못했습니다(백신이 검사 중이거나 다른 프로그램이 쓰는 중일 수 있습니다): %1
english.HomeCaption=Orcus folder
english.HomeDescription=Choose the folder for the virtual phone and the Android SDK.
english.HomeSub=This folder holds the program (its app folder) and the Android SDK and virtual phone that Orcus downloads the first time it runs (about 14 GB after setup, 24 GB while downloading). The path must be 40 characters or fewer in English letters and digits; avoid folders Google Drive synchronizes.
english.BadHome=Use a path that starts with a drive letter, in English letters and digits, 40 characters or fewer (for example D:\OrcusAPK).%nA drive's top folder (such as D:\) and removable or network drives cannot be used.
english.AssocApk=Add Orcus APK Emulator to "Open with" for .apk files
english.ApkFileType=Android app (APK)
english.DeleteDataQ=Also delete the virtual phone, the downloaded Android SDK, the logs and the settings left in %1 (about 14 GB)?%n%n[No]: keep them. A new install goes on with them.%n[Yes]: delete them. A new install downloads the SDK again and asks you to agree to the license again.
english.DeleteFailed=Some of it could not be deleted (an antivirus scan or another program may be using it): %1

[Tasks]
Name: "apkopenwith"; Description: "{cm:AssocApk}"
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked

[Files]
Source: "{#SourceDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{autoprograms}\Orcus APK Emulator"; Filename: "{app}\OrcusAPK.exe"; WorkingDir: "{app}"
Name: "{autodesktop}\Orcus APK Emulator"; Filename: "{app}\OrcusAPK.exe"; WorkingDir: "{app}"; Tasks: desktopicon

[Registry]
; "Open with" only: the default program for .apk stays the user's choice
Root: HKA; Subkey: "Software\Classes\OrcusAPK.apk"; ValueType: string; ValueName: ""; ValueData: "{cm:ApkFileType}"; Flags: uninsdeletekey; Tasks: apkopenwith
Root: HKA; Subkey: "Software\Classes\OrcusAPK.apk\DefaultIcon"; ValueType: string; ValueName: ""; ValueData: "{app}\OrcusAPK.exe,0"; Tasks: apkopenwith
Root: HKA; Subkey: "Software\Classes\OrcusAPK.apk\shell\open\command"; ValueType: string; ValueName: ""; ValueData: """{app}\OrcusAPK.exe"" ""%1"""; Tasks: apkopenwith
Root: HKA; Subkey: "Software\Classes\.apk"; Flags: uninsdeletekeyifempty; Tasks: apkopenwith
Root: HKA; Subkey: "Software\Classes\.apk\OpenWithProgids"; ValueType: string; ValueName: "OrcusAPK.apk"; ValueData: ""; Flags: uninsdeletevalue uninsdeletekeyifempty; Tasks: apkopenwith
Root: HKA; Subkey: "Software\Classes\Applications\OrcusAPK.exe"; ValueType: string; ValueName: "FriendlyAppName"; ValueData: "Orcus APK Emulator"; Flags: uninsdeletekey; Tasks: apkopenwith
Root: HKA; Subkey: "Software\Classes\Applications\OrcusAPK.exe\SupportedTypes"; ValueType: string; ValueName: ".apk"; ValueData: ""; Tasks: apkopenwith
Root: HKA; Subkey: "Software\Classes\Applications\OrcusAPK.exe\shell\open\command"; ValueType: string; ValueName: ""; ValueData: """{app}\OrcusAPK.exe"" ""%1"""; Tasks: apkopenwith

[UninstallDelete]
Type: files; Name: "{app}\orcus_home.txt"

[Run]
Filename: "{app}\OrcusAPK.exe"; Description: "{cm:LaunchProgram,Orcus APK Emulator}"; Flags: nowait postinstall skipifsilent

[Code]
const
  HomeFile = 'orcus_home.txt';
  MaxHome = 40;                                   { paths.MAX_HOME_LEN }
  DriveFixed = 3;
  RunOnceKey = 'Software\Microsoft\Windows\CurrentVersion\RunOnce';
  RunOnceName = 'OrcusAPKSetup';                  { setup.whpx.RUNONCE_NAME }
  DataDirs = 'sdk|avd|home|tmp|downloads|staging|logs|state|diag';   { common.paths, app.diag }

var
  HomePage: TInputDirWizardPage;

function GetDriveType(lpRootPathName: String): Cardinal;
  external 'GetDriveTypeW@kernel32.dll stdcall';

function DefaultHome(): String;
begin
  if GetDriveType('D:\') = DriveFixed then
    Result := 'D:\OrcusAPK'
  else
    Result := ExpandConstant('{sd}') + '\OrcusAPK';
end;

function DefaultAppDir(Param: String): String;
var
  Home: String;
begin
  Home := ExpandConstant('{param:HOME|}');
  if Home = '' then
    Home := DefaultHome();
  Result := RemoveBackslashUnlessRoot(Home) + '\app';
end;

{ The same rules as paths.home_problems that an installer can check: a local fixed drive, ASCII, at most 40
  characters, not a drive's top folder. The program checks NTFS and Google Drive again when setup starts. }
function ValidHome(Path: String): Boolean;
var
  I: Integer;
  C: Integer;
begin
  Result := False;
  if (Length(Path) < 4) or (Length(Path) > MaxHome) then
    Exit;
  if (Path[2] <> ':') or (Path[3] <> '\') then
    Exit;
  for I := 1 to Length(Path) do
  begin
    C := Ord(Path[I]);
    if (C < 32) or (C > 126) then
      Exit;
    if Pos(Path[I], '/*?"<>|') > 0 then
      Exit;
  end;
  if Pos(':', Copy(Path, 3, MaxInt)) > 0 then
    Exit;
  Result := GetDriveType(Copy(Path, 1, 3)) = DriveFixed;
end;

procedure InitializeWizard();
begin
  HomePage := CreateInputDirPage(wpWelcome, CustomMessage('HomeCaption'), CustomMessage('HomeDescription'),
    CustomMessage('HomeSub'), False, '');
  HomePage.Add('');
  HomePage.Values[0] := ExtractFileDir(WizardForm.DirEdit.Text);   { the last install's folder, or the default }
end;

function NextButtonClick(CurPageID: Integer): Boolean;
var
  Home: String;
begin
  Result := True;
  if CurPageID = HomePage.ID then
  begin
    Home := RemoveBackslashUnlessRoot(Trim(HomePage.Values[0]));
    if not ValidHome(Home) then
    begin
      MsgBox(CustomMessage('BadHome'), mbError, MB_OK);
      Result := False;
      Exit;
    end;
    HomePage.Values[0] := Home;
    WizardForm.DirEdit.Text := Home + '\app';
  end;
end;

function InitializeSetup(): Boolean;
var
  Home: String;
begin
  Result := True;
  Home := ExpandConstant('{param:HOME|}');
  if (Home <> '') and not ValidHome(RemoveBackslashUnlessRoot(Home)) then
  begin
    SuppressibleMsgBox(CustomMessage('BadHome'), mbError, MB_OK, IDOK);
    Result := False;
  end;
end;

procedure CurStepChanged(CurStep: TSetupStep);
begin
  if CurStep = ssPostInstall then
    SaveStringToFile(ExpandConstant('{app}\') + HomeFile, ExtractFileDir(ExpandConstant('{app}')), False);
end;

{ Orcus's own subfolders of the Orcus folder, one at a time ('' ends the list). }
function DataDir(var Rest: String): String;
var
  P: Integer;
begin
  Result := '';
  if Rest = '' then
    Exit;
  P := Pos('|', Rest);
  if P = 0 then
  begin
    Result := Rest;
    Rest := '';
  end
  else
  begin
    Result := Copy(Rest, 1, P - 1);
    Rest := Copy(Rest, P + 1, MaxInt);
  end;
end;

function HasData(Home: String): Boolean;
var
  Rest, Name: String;
begin
  Result := False;
  if Length(Home) <= 3 then   { never a drive's top folder }
    Exit;
  Rest := DataDirs;
  Name := DataDir(Rest);
  while Name <> '' do
  begin
    if DirExists(Home + '\' + Name) then
      Result := True;
    Name := DataDir(Rest);
  end;
end;

procedure DeleteData(Home: String);
var
  Rest, Name, Failed: String;
begin
  Failed := '';
  Rest := DataDirs;
  Name := DataDir(Rest);
  while Name <> '' do
  begin
    if DirExists(Home + '\' + Name) and not DelTree(Home + '\' + Name, True, True, True) then
      Failed := Failed + ' ' + Name;
    Name := DataDir(Rest);
  end;
  RemoveDir(Home);   { only when nothing else is left in it }
  if Failed <> '' then
    SuppressibleMsgBox(FmtMessage(CustomMessage('DeleteFailed'), [Home + ':' + Failed]), mbError, MB_OK, IDOK);
end;

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
var
  App, Home: String;
begin
  if CurUninstallStep = usUninstall then
    RegDeleteValue(HKCU, RunOnceKey, RunOnceName);   { a setup that waited for a restart }
  if CurUninstallStep <> usPostUninstall then
    Exit;
  App := ExpandConstant('{app}');
  Home := ExtractFileDir(App);
  if (CompareText(ExtractFileName(App), 'app') <> 0) or not HasData(Home) then
    Exit;
  if ExpandConstant('{param:DELETEDATA|0}') = '1' then
    DeleteData(Home)
  else if SuppressibleMsgBox(FmtMessage(CustomMessage('DeleteDataQ'), [Home]), mbConfirmation,
                             MB_YESNO or MB_DEFBUTTON2, IDNO) = IDYES then
    DeleteData(Home);
end;
