# 创建桌面快捷方式：直接调用项目 .venv 的 pythonw.exe，不经过 exe，
# 因此不会触发 Microsoft Defender SmartScreen。
$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
$pythonw = Join-Path $root '.venv\Scripts\pythonw.exe'
if (-not (Test-Path -LiteralPath $pythonw)) {
    Write-Error "找不到 $pythonw，请先运行 .venv\Scripts\pip install -e ."
    exit 1
}
$desktop = [Environment]::GetFolderPath('Desktop')
$shortcutPath = Join-Path $desktop 'mkr.lnk'
$shell = New-Object -ComObject WScript.Shell
$sc = $shell.CreateShortcut($shortcutPath)
$sc.TargetPath = $pythonw
$sc.Arguments = '-m mkr.gui'
$sc.WorkingDirectory = $root
$sc.WindowStyle = 7
$sc.Description = 'mkr 胶囊录制回放'
$icon = Join-Path $root 'assets\ico.ico'
if (Test-Path -LiteralPath $icon) {
    $sc.IconLocation = $icon
}
$sc.Save()
Write-Output "已创建桌面快捷方式: $shortcutPath"
Write-Output "双击该快捷方式即可启动，不会触发 SmartScreen。"
