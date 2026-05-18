; 绮绮采集器 - Inno Setup 安装脚本
; 编译方式：用 Inno Setup 6.x 打开此文件 → 点 Build → Compile
; 安装到用户目录（%LOCALAPPDATA%），无需管理员权限

#define AppName    "绮绮采集器"
#define AppNameEn  "QiQiCollector"
#define AppVersion "2.9"
#define AppPublisher "QiQi Dev"
#define AppExeName "QiQiCollector.exe"

[Setup]
AppId={{B7A2F3E1-4C8D-4A9B-8F3E-1D2C3A4B5C6D}
AppName={#AppName}
AppVersion={#AppVersion}
AppPublisherURL=
AppSupportURL=
AppUpdatesURL=
; 安装到用户目录，无需 UAC 弹框
DefaultDirName={localappdata}\{#AppNameEn}
DefaultGroupName={#AppName}
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog
; 允许用户自定义路径，但建议用默认
DisableProgramGroupPage=yes
; 压缩设置
Compression=lzma2/ultra64
SolidCompression=yes
; 输出
OutputDir=dist_installer
OutputBaseFilename={#AppNameEn}_v{#AppVersion}_Setup
; 图标
SetupIconFile=assets\logo.ico
; 向导外观
WizardStyle=modern
WizardSizePercent=120
; 卸载
Uninstallable=yes
UninstallDisplayName={#AppName}
UninstallDisplayIcon={app}\{#AppExeName}
; 语言
ShowLanguageDialog=no

[Languages]
Name: "chs"; MessagesFile: "compiler:Languages\ChineseSimplified.isl"

[Tasks]
Name: "desktopicon"; Description: "创建桌面快捷方式"; GroupDescription: "快捷方式:"; Flags: checkedonce
Name: "startmenuicon"; Description: "在开始菜单中添加快捷方式"; GroupDescription: "快捷方式:"; Flags: checkedonce

[Files]
; 主程序
Source: "dist\{#AppExeName}"; DestDir: "{app}"; Flags: ignoreversion

; 服务器地址配置文件（用户可自行修改）
Source: "license_server.txt"; DestDir: "{app}"; Flags: ignoreversion onlyifdoesntexist

; 图标资源
Source: "assets\logo.ico"; DestDir: "{app}\assets"; Flags: ignoreversion
Source: "assets\logo.png"; DestDir: "{app}\assets"; Flags: ignoreversion

; JS 注入脚本（sign_server 运行时需要）
Source: "static\xhs_main.js"; DestDir: "{app}\static"; Flags: ignoreversion
Source: "static\xhs_rap.js";  DestDir: "{app}\static"; Flags: ignoreversion
Source: "static\xhs_xray.js"; DestDir: "{app}\static"; Flags: ignoreversion
Source: "static\sign_server.js"; DestDir: "{app}\static"; Flags: ignoreversion
Source: "static\node.exe"; DestDir: "{app}\static"; Flags: ignoreversion
Source: "static\node_modules\*"; DestDir: "{app}\static\node_modules"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
; 开始菜单
Name: "{group}\{#AppName}"; Filename: "{app}\{#AppExeName}"; WorkingDir: "{app}"
Name: "{group}\卸载 {#AppName}"; Filename: "{uninstallexe}"

; 桌面快捷方式
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#AppExeName}"; WorkingDir: "{app}"; Tasks: desktopicon

[Run]
; 安装完成后可选立即启动
Filename: "{app}\{#AppExeName}"; Description: "立即启动 {#AppName}"; Flags: nowait postinstall skipifsilent

[UninstallDelete]
; 卸载时删除程序目录（不删除用户数据）
Type: filesandordirs; Name: "{app}\static"
Type: filesandordirs; Name: "{app}\assets"

[Code]
// 安装时检查旧版本是否正在运行，提示关闭
function InitializeSetup(): Boolean;
var
  ResultCode: Integer;
begin
  Result := True;
  if CheckForMutexes('QiQiCollector_Running') then begin
    if MsgBox('检测到绮绮采集器正在运行，请先关闭再继续安装。'
              + #13#10 + '点击"确定"继续（可能导致安装不完整），点击"取消"退出安装。',
              mbConfirmation, MB_OKCANCEL) = IDCANCEL then
      Result := False;
  end;
end;

// 升级时保留用户配置文件（~/.qiqi_license.dat 等在用户目录，不需特别处理）
procedure CurStepChanged(CurStep: TSetupStep);
begin
  // 预留：安装完成后可在此写注册表或初始化配置
end;
