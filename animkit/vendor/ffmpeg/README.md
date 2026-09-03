# Bundled ffmpeg

animkit converts a dropped video into an image sequence before handing it to
Maya, because **Maya cannot decode `.mp4` or `.mov` on an image plane at all**.
That is measured, not assumed — on Maya 2024 / Windows, `imagePlane.coverageX`
stays `-1` for both, in both Movie and Image File mode, so the plane loads and
draws nothing. Only a MJPEG `.avi` reads natively.

Doing that conversion needs ffmpeg, and Maya does not ship one. So animkit
does.

## What is here

```
ffmpeg/
  README.md          this file
  LICENSE            the licence the bundled binary is under
  win64/ffmpeg.exe   Windows x64
  macos/             empty slot — drop a macOS build in and it is found
  linux/             empty slot
```

`animkit.core.transcode.executable()` looks in this order:

1. the `reference.ffmpeg` setting, if a studio has standardised on a build;
2. the binary bundled here for the current platform;
3. whatever `ffmpeg` is on `PATH`.

Nothing here is required. With no ffmpeg at all, animkit falls back to handing
the movie to Maya exactly as it used to, and says so — images and image
sequences are unaffected either way.

## Licence, and why you might want to replace this build

The bundled Windows binary is **ffmpeg 8.1.1** from
[gyan.dev](https://www.gyan.dev/ffmpeg/builds/), built with `--enable-gpl
--enable-version3`. It is therefore under the **GPL v3** (`LICENSE` in this
folder), and redistributing it carries the GPL's obligations — including
making the corresponding source available to anyone you give it to. FFmpeg's
sources are at <https://git.ffmpeg.org/ffmpeg.git>, tag `n8.1.1`.

Invoking it as a **separate program over a subprocess** is aggregation, not
linking, so this does not make animkit itself GPL. But if animkit is
redistributed outside your studio, the ffmpeg binary is the part that comes
with strings.

**An LGPL build would be the better choice** and is a drop-in replacement.
animkit only ever *decodes* video and writes jpg/png — it uses no GPL-only
encoder — so an LGPL "shared" or "essentials" build does everything needed, is
considerably smaller, and is far simpler to redistribute. To swap it:

1. get an LGPL build for the platform;
2. put its `ffmpeg` / `ffmpeg.exe` in the folder for that platform;
3. replace `LICENSE` with the one that build ships;
4. run `.\scripts\run_tests.ps1` — `tests/test_transcode.py` runs real
   conversions against whatever binary is here, so it will tell you
   immediately if the build cannot do the job.

## Size

The bundled Windows build is a ~217 MB static binary, which dwarfs the ~500 KB
of Python around it. That is the cost of the tool working out of the box on a
machine with nothing installed. If that trade is wrong for your pipeline,
delete the binary and set `reference.ffmpeg` to a shared one, or rely on
`PATH` — everything still works, and only the "no setup required" property is
lost.

## macOS and Linux

The slots are empty because no build for them has been tested here. Dropping a
binary in is all that is required — the lookup is per platform and the code
path is identical. Note that a file copied to macOS usually needs
`chmod +x` and may need to be cleared of the quarantine attribute
(`xattr -d com.apple.quarantine ffmpeg`) before it will run.
