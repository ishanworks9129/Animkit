<#
.SYNOPSIS
    Run the animkit test suite.

.DESCRIPTION
    Two tiers:
      (default)  fast tier -- blend maths, settings, radial geometry,
                 dropped-media parsing and video conversion (runs the
                 bundled ffmpeg), plain CPython, seconds
      -Maya      full tier -- boots maya.standalone via mayapy, builds scenes

    Run the fast tier while you work. Run the full tier before every commit,
    and against every rig in your test set before every release.

.EXAMPLE
    .\scripts\run_tests.ps1
    .\scripts\run_tests.ps1 -Maya
    .\scripts\run_tests.ps1 -Maya -MayaVersion 2026
#>
param(
    [switch]$Maya,
    [string]$MayaVersion = "2024",
    [string]$MayaRoot = "C:\Program Files\Autodesk"
)

$ErrorActionPreference = "Stop"
$repo = Split-Path -Parent $PSScriptRoot

if (-not $Maya) {
    Write-Host "Fast tier (no Maya)" -ForegroundColor Cyan
    & python -m pytest "$repo\tests\test_blend.py" "$repo\tests\test_settings.py" "$repo\tests\test_radial_geom.py" "$repo\tests\test_media.py" "$repo\tests\test_transcode.py" "$repo\tests\test_catalogue.py" "$repo\tests\test_usage.py" "$repo\tests\test_install.py" -q
    exit $LASTEXITCODE
}

$mayapy = Join-Path $MayaRoot "Maya$MayaVersion\bin\mayapy.exe"
if (-not (Test-Path $mayapy)) {
    Write-Error "mayapy not found at $mayapy. Pass -MayaVersion or -MayaRoot."
}

Write-Host "Full tier via $mayapy" -ForegroundColor Cyan

# pytest is not bundled with mayapy. Install per-user, NOT into Maya's own
# site-packages: Maya lives under Program Files, so a plain install needs an
# elevated shell. --user drops it in %APPDATA%\Python, which mayapy also reads.
# Probe with find_spec, not "import pytest". A failed import writes a traceback
# to stderr, and in PowerShell 5.1 a native exe's stderr gets wrapped in a
# NativeCommandError -- which $ErrorActionPreference="Stop" then treats as
# terminating, killing this script before it can install anything. find_spec
# writes nothing and just sets an exit code.
& $mayapy -c "import importlib.util, sys; sys.exit(0 if importlib.util.find_spec('pytest') else 1)"
if ($LASTEXITCODE -ne 0) {
    Write-Host "Installing pytest for $MayaVersion (one-off)..." -ForegroundColor Yellow
    & $mayapy -m pip install --user --quiet pytest
    if ($LASTEXITCODE -ne 0) {
        Write-Error "Could not install pytest. Try running: `"$mayapy`" -m pip install --user pytest"
    }
}

$env:PYTHONPATH = "$repo;$env:PYTHONPATH"
& $mayapy -m pytest "$repo\tests" -q
exit $LASTEXITCODE
