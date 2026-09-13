# Installs / updates the add-on for one or more Blender versions by creating a
# directory junction from the user add-on folder to this repository, so edits in
# the repo are live after "Reload Scripts" (or restarting Blender).
#
#   .\install.ps1              # Blender 3.6
#   .\install.ps1 3.6,4.2      # several versions
#   .\install.ps1 -Copy        # copy instead of junction
param(
    [string[]]$Versions = @('3.6'),
    [switch]$Copy
)
$src = Join-Path $PSScriptRoot 'blender2xps'
foreach ($v in $Versions) {
    $addons = Join-Path $env:APPDATA "Blender Foundation\Blender\$v\scripts\addons"
    if (-not (Test-Path $addons)) { New-Item -ItemType Directory -Force $addons | Out-Null }
    $dst = Join-Path $addons 'blender2xps'
    if (Test-Path $dst) {
        $item = Get-Item $dst -Force
        if ($item.Attributes -band [IO.FileAttributes]::ReparsePoint) { $item.Delete() } else { Remove-Item -Recurse -Force $dst }
    }
    if ($Copy) {
        Copy-Item -Recurse $src $dst
        Get-ChildItem -Recurse -Directory -Filter '__pycache__' $dst | Remove-Item -Recurse -Force
        Write-Host "copied  -> $dst"
    } else {
        New-Item -ItemType Junction -Path $dst -Target $src | Out-Null
        Write-Host "junction $dst -> $src"
    }
}
Write-Host 'Now enable "Blender2XPS" in Edit > Preferences > Add-ons (or run tools/enable_in_blender.py through blender-mcp).'
