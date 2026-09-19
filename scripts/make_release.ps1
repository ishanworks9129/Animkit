<#
.SYNOPSIS
    Build the animkit hand-off package: a folder and a zip that a tester
    unzips and drags into Maya.

.DESCRIPTION
    Copies only what a tester needs, and deliberately leaves out:

      tests/            7,600 lines that only run under mayapy
      scripts/          bench.py, rig_probe.py, this file -- dev tooling
      AGENTS.md         agent orientation, meaningless outside the repo
      README.md         the design reasoning. 91 KB of why-we-did-it-this-way.
                        Pass -IncludeDocs to ship it; think about whether you
                        want to hand that to the party you are shipping to.
      __pycache__       .pyc files compiled against YOUR Python, which are
                        worse than useless on a different Maya version
      .git, .gitignore

    The bundled ffmpeg binary is EXCLUDED by default and this is not just
    about the 217 MB. The bundled Windows build is GPL v3, so handing it to
    somebody else brings the GPL's obligations with it -- including making
    ffmpeg's corresponding source available to them. Options, best first:

      1. Drop an LGPL build into animkit/vendor/ffmpeg/win64/ and ship that
         (-IncludeFFmpeg). animkit only ever decodes video and writes
         jpg/png, so an LGPL "shared" build does everything needed and is far
         smaller. See animkit/vendor/ffmpeg/README.md.
      2. Ship without it (the default). Everything works except decoding
         .mp4/.mov for reference, which falls back to whatever ffmpeg is on
         PATH, or to handing the movie straight to Maya.
      3. Ship the GPL build knowingly (-IncludeFFmpeg) and meet the
         obligation. The LICENSE file travels with it either way.

    -Recipient stamps the package with who it was cut for, by rewriting
    animkit/_build.py in the staged copy. Cut one zip per recipient and a
    usage log that comes back -- or a copy that turns up somewhere it was not
    sent -- can be matched to exactly which handover it came from. It costs a
    rebuild per recipient, and it is the only tracking here that needs
    nothing running on their machine.

.EXAMPLE
    .\scripts\make_release.ps1
    .\scripts\make_release.ps1 -Recipient "animbot"
    .\scripts\make_release.ps1 -Recipient "studio-x" -IncludeFFmpeg
    .\scripts\make_release.ps1 -IncludeDocs -Version 0.1.0-eval
#>
param(
    [string]$Recipient,
    [string]$Version,
    [switch]$IncludeFFmpeg,
    [switch]$IncludeDocs,
    [string]$OutputDir
)

$ErrorActionPreference = "Stop"
$repo = Split-Path -Parent $PSScriptRoot

# Windows PowerShell 5.1's `Set-Content -Encoding utf8` ALWAYS writes a BOM.
# Python tolerates one at the top of a source file; Maya's .mod parser is C++
# and has no reason to, and a module file that fails to parse is a tool that
# silently does not load on the machine you were trying to impress.
$utf8NoBom = New-Object System.Text.UTF8Encoding $false
function Write-Plain($Path, $Text) {
    [System.IO.File]::WriteAllText($Path, $Text, $utf8NoBom)
}

# Single source of truth for the version: animkit/__init__.py.
if (-not $Version) {
    $initText = Get-Content (Join-Path $repo "animkit\__init__.py") -Raw
    if ($initText -match '__version__\s*=\s*"([^"]+)"') {
        $Version = $Matches[1]
    } else {
        Write-Error "Could not read __version__ from animkit\__init__.py"
    }
}

# Filesystem-safe, lowercase, no spaces: the slug ends up in a filename, in
# the build id, and in every session line of a usage log that comes back.
$slug = ""
if ($Recipient) {
    $slug = ($Recipient -replace '[^A-Za-z0-9._-]', '-').ToLower().Trim('-')
}

if (-not $OutputDir) { $OutputDir = Join-Path $repo "dist" }
$name    = if ($slug) { "animkit-$Version-$slug" } else { "animkit-$Version" }
$buildId = if ($slug) { "$Version-$slug" } else { $Version }
$stage   = Join-Path $OutputDir $name
$zip     = Join-Path $OutputDir "$name.zip"

Write-Host "Building $name" -ForegroundColor Cyan
if ($Recipient) { Write-Host "  for  : $Recipient" -ForegroundColor Cyan }
Write-Host "  from : $repo"
Write-Host "  into : $stage"

if (Test-Path $stage) { Remove-Item $stage -Recurse -Force }
if (Test-Path $zip)   { Remove-Item $zip -Force }
New-Item -ItemType Directory -Path $stage -Force | Out-Null

# --- the package -----------------------------------------------------------
# Copy, then prune. Simpler to read than a filtered recursive copy, and the
# prune step is the one that has to be right.
Copy-Item (Join-Path $repo "animkit") -Destination $stage -Recurse

Get-ChildItem $stage -Recurse -Force -Directory -Filter "__pycache__" |
    Remove-Item -Recurse -Force
Get-ChildItem $stage -Recurse -Force -Include "*.pyc", "*.pyo" |
    Remove-Item -Force

# --- ffmpeg ----------------------------------------------------------------
$ffmpegDir = Join-Path $stage "animkit\vendor\ffmpeg"
$binaries  = @("win64\ffmpeg.exe", "macos\ffmpeg", "linux\ffmpeg")

if ($IncludeFFmpeg) {
    $shipped = $binaries | Where-Object { Test-Path (Join-Path $ffmpegDir $_) }
    if ($shipped) {
        foreach ($one in $shipped) {
            $size = [math]::Round((Get-Item (Join-Path $ffmpegDir $one)).Length / 1MB)
            Write-Host "  ffmpeg: shipping $one ($size MB)" -ForegroundColor Yellow
        }
        Write-Host "  ffmpeg: CHECK THE LICENCE of the build you are shipping." -ForegroundColor Yellow
        Write-Host "          A GPL v3 build obliges you to offer its source to" -ForegroundColor Yellow
        Write-Host "          whoever you hand this to. See vendor/ffmpeg/README.md." -ForegroundColor Yellow
    } else {
        Write-Host "  ffmpeg: -IncludeFFmpeg passed but no binary is present" -ForegroundColor Yellow
    }
} else {
    foreach ($one in $binaries) {
        $path = Join-Path $ffmpegDir $one
        if (Test-Path $path) { Remove-Item $path -Force }
    }
    Write-Host "  ffmpeg: excluded (licence + size). Video reference falls back to PATH."
}

# --- build identity --------------------------------------------------------
# Rewrite the STAGED _build.py. The working copy's stays at "dev", which is
# what animkit.core.usage reports when nobody has cut a package.
$cutFor = if ($Recipient) { $Recipient } else { "no named recipient" }
$quote  = '"' * 3
$stamped = @"
$quote Which build this is. WRITTEN BY scripts/make_release.ps1 -- DO NOT EDIT.

Cut for: $cutFor

A usage log that comes back carries these three values, so it can be matched
to the exact zip it came from without asking anybody to remember. So can a
copy that turns up somewhere it was not sent.
$quote

#: Short id for this build.
ID = "$buildId"

#: Who the package was cut for. Empty when -Recipient was not passed.
RECIPIENT = "$Recipient"

#: When it was cut, YYYY-MM-DD.
DATE = "$(Get-Date -Format 'yyyy-MM-dd')"
"@
Write-Plain (Join-Path $stage "animkit\_build.py") $stamped
Write-Host "  build id: $buildId"

# --- everything else -------------------------------------------------------
Copy-Item (Join-Path $repo "startup") -Destination $stage -Recurse
Get-ChildItem (Join-Path $stage "startup") -Recurse -Force -Directory -Filter "__pycache__" |
    Remove-Item -Recurse -Force

New-Item -ItemType Directory -Path (Join-Path $stage "modules") -Force | Out-Null

# The shipped .mod carries a placeholder, not this machine's path. A tester
# who opens it and sees C:/Users/<someone>/Desktop has learned something
# about how carefully the package was put together.
$modSource = Get-Content (Join-Path $repo "modules\animkit.mod") -Raw
$modSource = $modSource -replace [regex]::Escape("C:/Users/ishan/Desktop/Animbot"), "C:/path/to/$name"
$modSource = $modSource -replace '\+ animkit [^\s]+ ', "+ animkit $Version "
Write-Plain (Join-Path $stage "modules\animkit.mod") $modSource

Copy-Item (Join-Path $repo "DRAG_AND_DROP_INSTALL.py") -Destination $stage
Copy-Item (Join-Path $repo "release\README_FIRST.md")   -Destination $stage
Copy-Item (Join-Path $repo "userSetup_example.py")      -Destination $stage

if ($IncludeDocs) {
    Copy-Item (Join-Path $repo "README.md") -Destination $stage
    Write-Host "  docs: README.md included (the design reasoning -- your call)" -ForegroundColor Yellow
}

# --- checks ----------------------------------------------------------------
# Cheap, and they catch the two failures that waste a tester's afternoon:
# a package that cannot import, and a personal path left in a shipped file.
$problems = @()

foreach ($required in @("DRAG_AND_DROP_INSTALL.py", "README_FIRST.md",
                        "animkit\__init__.py", "animkit\ui\panel.py",
                        "animkit\core\usage.py", "animkit\_build.py",
                        "startup\userSetup.py", "modules\animkit.mod")) {
    if (-not (Test-Path (Join-Path $stage $required))) {
        $problems += "missing: $required"
    }
}

# Separators are matched one-or-more, not exactly one: the dev scripts write
# their example path as a Python string literal with DOUBLED backslashes, and
# a pattern expecting a single one sails straight past the very thing this
# check exists to catch.
$personal = @(
    [regex]::Escape($env:USERNAME),          # whoever is building
    'Users[\\/]+[^\\/"]+[\\/]+Desktop'       # anybody's Desktop
)
$packaged = Get-ChildItem $stage -Recurse -File -Include "*.py", "*.mod", "*.md"
foreach ($pattern in $personal) {
    foreach ($hit in ($packaged | Select-String -Pattern $pattern -List)) {
        $problems += "personal path in: $($hit.Path.Substring($stage.Length + 1)) -- $($hit.Line.Trim())"
    }
}

$stray = Get-ChildItem $stage -Recurse -Force -Include "*.pyc", "__pycache__"
foreach ($one in $stray) { $problems += "stray build artefact: $($one.Name)" }

# Does the package actually import? py_compile every file, which catches a
# syntax error that only bites on a Python this machine does not have.
& python -c "import compileall, sys; sys.exit(0 if compileall.compile_dir(r'$stage', quiet=2, force=True) else 1)" | Out-Null
if ($LASTEXITCODE -ne 0) { $problems += "a file in the package does not compile" }
Get-ChildItem $stage -Recurse -Force -Directory -Filter "__pycache__" | Remove-Item -Recurse -Force

# The stamp has to survive the staging, or every log that comes back says
# "dev" and the whole per-recipient exercise was for nothing.
$stampedText = Get-Content (Join-Path $stage "animkit\_build.py") -Raw
if ($stampedText -notmatch [regex]::Escape("ID = `"$buildId`"")) {
    $problems += "animkit\_build.py was not stamped with $buildId"
}

# One file can trip more than one pattern; report it once.
$problems = @($problems | Select-Object -Unique)

if ($problems) {
    Write-Host ""
    Write-Host "Package is NOT clean:" -ForegroundColor Red
    foreach ($problem in $problems) { Write-Host "  $problem" -ForegroundColor Red }
    Write-Error "Refusing to zip a package with $($problems.Count) problem(s)."
}

# --- zip -------------------------------------------------------------------
Compress-Archive -Path $stage -DestinationPath $zip -CompressionLevel Optimal

$zipSize   = [math]::Round((Get-Item $zip).Length / 1MB, 1)
$fileCount = (Get-ChildItem $stage -Recurse -File).Count

Write-Host ""
Write-Host "Done." -ForegroundColor Green
Write-Host "  $zip"
Write-Host "  $fileCount files, $zipSize MB zipped, build id $buildId"
Write-Host ""
Write-Host "Hand over the zip. The tester unzips it somewhere permanent, starts"
Write-Host "Maya, and drags DRAG_AND_DROP_INSTALL.py into a viewport."

if (-not $Recipient) {
    Write-Host ""
    Write-Host "No -Recipient passed, so this build is stamped '$buildId' and is" -ForegroundColor Yellow
    Write-Host "indistinguishable from every other copy. Cut one zip per recipient if" -ForegroundColor Yellow
    Write-Host "you want to tell their feedback -- or their leak -- apart." -ForegroundColor Yellow
}
