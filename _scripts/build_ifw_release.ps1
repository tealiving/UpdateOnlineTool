param(
    [Parameter(Mandatory = $true)]
    [string] $MainRepo,

    [Parameter(Mandatory = $true)]
    [string] $IfwRoot,

    [string] $Channel = "stable",

    [string] $SettingsFile = "config\settings.json",

    [ValidateSet("github", "gitee", "devops")]
    [string] $ExternalProvider = "github",

    [ValidateSet("github", "gitee", "devops")]
    [string] $InternalProvider = "devops"
)

$ErrorActionPreference = "Stop"

function Get-FullPath([string] $PathValue) {
    if ([System.IO.Path]::IsPathRooted($PathValue)) {
        return [System.IO.Path]::GetFullPath($PathValue)
    }

    return [System.IO.Path]::GetFullPath((Join-Path (Get-Location) $PathValue))
}

function Assert-ChildPath([string] $ParentPath, [string] $ChildPath) {
    $parentFullPath = (Get-FullPath $ParentPath).TrimEnd('\') + '\'
    $childFullPath = Get-FullPath $ChildPath

    if (-not $childFullPath.StartsWith($parentFullPath, [System.StringComparison]::OrdinalIgnoreCase)) {
        throw "Refusing to modify path outside release root: $childFullPath"
    }

    return $childFullPath
}

$mainRepoFullPath = Get-FullPath $MainRepo
if (-not (Test-Path $mainRepoFullPath)) {
    throw "Main repo not found: $mainRepoFullPath"
}

$settingsFullPath = Get-FullPath $SettingsFile
if (-not (Test-Path $settingsFullPath)) {
    throw "Settings file not found: $settingsFullPath"
}

$releaseRoot = Get-FullPath (Join-Path (Get-Location) "automation-manual-studio\$Channel")
$repositoryTarget = Assert-ChildPath $releaseRoot (Join-Path $releaseRoot "ifw-repository")
$installerTarget = Assert-ChildPath $releaseRoot (Join-Path $releaseRoot "installer")
$externalConfig = Join-Path $mainRepoFullPath "out\ifw-config\config.external.resolved.xml"
$internalConfig = Join-Path $mainRepoFullPath "out\ifw-config\config.internal.resolved.xml"

Push-Location $mainRepoFullPath
try {
    $appVersion = (rtk proxy python -c "from app.application.api.version import APP_VERSION; print(APP_VERSION)").Trim()
    $distDir = "dist\AutomationManualStudio_release_v$appVersion"

    rtk proxy powershell -NoProfile -ExecutionPolicy Bypass -File tools\ifw\Build-IfwPackageTree.ps1 -DistDir $distDir -SettingsFile $settingsFullPath
    rtk proxy powershell -NoProfile -ExecutionPolicy Bypass -File tools\ifw\Build-IfwRepository.ps1 -IfwRoot $IfwRoot
    rtk proxy powershell -NoProfile -ExecutionPolicy Bypass -File tools\ifw\New-IfwResolvedConfig.ps1 -SourceConfig installer\ifw\config\config.external.xml -OutputConfig $externalConfig -SettingsFile $settingsFullPath -Provider $ExternalProvider -Channel $Channel
    rtk proxy powershell -NoProfile -ExecutionPolicy Bypass -File tools\ifw\New-IfwResolvedConfig.ps1 -SourceConfig installer\ifw\config\config.internal.xml -OutputConfig $internalConfig -SettingsFile $settingsFullPath -Provider $InternalProvider -Channel $Channel
    rtk proxy powershell -NoProfile -ExecutionPolicy Bypass -File tools\ifw\Build-IfwInstaller.ps1 -IfwRoot $IfwRoot -Profile external -Mode hybrid -ConfigPath $externalConfig
    rtk proxy powershell -NoProfile -ExecutionPolicy Bypass -File tools\ifw\Build-IfwInstaller.ps1 -IfwRoot $IfwRoot -Profile internal -Mode hybrid -ConfigPath $internalConfig
    rtk proxy powershell -NoProfile -ExecutionPolicy Bypass -File tools\ifw\Build-IfwInstaller.ps1 -IfwRoot $IfwRoot -Profile internal -Mode offline
}
finally {
    Pop-Location
}

$repositorySource = Join-Path $mainRepoFullPath "out\ifw-repository"
$installerSource = Join-Path $mainRepoFullPath "out\ifw"

if (-not (Test-Path (Join-Path $repositorySource "Updates.xml"))) {
    throw "IFW repository Updates.xml not found: $repositorySource"
}

$installerArtifacts = Get-ChildItem -Path $installerSource -Filter *.exe -ErrorAction SilentlyContinue
if (-not $installerArtifacts) {
    throw "No IFW installer artifacts found: $installerSource"
}

Remove-Item -LiteralPath $repositoryTarget -Recurse -Force -ErrorAction SilentlyContinue
New-Item -ItemType Directory -Path $repositoryTarget -Force | Out-Null
Copy-Item -Path (Join-Path $repositorySource "*") -Destination $repositoryTarget -Recurse -Force

New-Item -ItemType Directory -Path $installerTarget -Force | Out-Null
Copy-Item -Path (Join-Path $installerSource "*.exe") -Destination $installerTarget -Force

rtk proxy python _scripts\verify_ifw_repository.py $repositoryTarget

Write-Host "IFW release copied to: $releaseRoot"
