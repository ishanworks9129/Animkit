# Bundled ffmpeg

animkit converts a dropped video into an image sequence before handing it to
Maya, because **Maya cannot decode `.mp4` or `.mov` on an image plane at all**.
That is measured, not assumed — on Maya 2024 / Windows, `imagePlane.coverageX`
stays `-1` for both, in both Movie and Image File mode, so the plane loads and
draws nothing. Only a MJPEG `.avi` reads natively.

It converts a dropped `.mp3` for the same kind of reason: Maya's audio node
reads wav and aiff and nothing else.

Both need ffmpeg, and Maya does not ship one. So animkit does.

## What is here

```
ffmpeg/
  README.md          this file
  LICENSE            LGPL v3 — the licence the bundled binary is under
  win64/ffmpeg.exe   Windows x64
  macos/             empty slot — drop a macOS build in and it is found
  linux/             empty slot
```

`animkit.core.transcode.executable()` looks in this order:

1. the `reference.ffmpeg` setting, if a studio has standardised on a build;
2. the binary bundled here for the current platform;
3. whatever `ffmpeg` is on `PATH`.

Nothing here is required. With no ffmpeg at all, a dropped video still makes a
plane — it just stays blank, and says why — and images and image sequences are
unaffected either way.

`ffprobe` is deliberately **not** bundled. Duration and frame rate are parsed
out of `ffmpeg -i`'s own stderr, which saves shipping a second 126 MB binary
to answer two questions.

## The bundled build, exactly

| | |
|---|---|
| Version | `n8.1.2-54-gc573a95381`, built 2026-09-18 |
| Source of the build | [BtbN/FFmpeg-Builds](https://github.com/BtbN/FFmpeg-Builds), release `latest`, asset `ffmpeg-n8.1-latest-win64-lgpl-8.1.zip` |
| Licence | **LGPL v3** — configured `--enable-version3` with **no** `--enable-gpl` |
| Size | 126 MB, static, one file with no DLLs beside it |

### Why LGPL and not the smaller-sounding alternatives

The obvious choice looks like a GPL "essentials" build, and it is the wrong
one. A GPL build obliges whoever redistributes animkit to offer ffmpeg's
corresponding source to everyone they hand it to — which turns shipping an
evaluation copy to a studio into a licence-compliance exercise. An LGPL build
carries a much lighter obligation and costs animkit nothing, because **animkit
only ever decodes**.

Everything animkit asks ffmpeg to do is LGPL-clean:

- decoding H.264 / H.265 / VP9 / AV1 out of mp4 and mov — the native decoders
  are LGPL. Only the *encoders* `libx264` and `libx265` are GPL, and this
  build has them explicitly disabled;
- encoding MJPEG and PNG — native;
- decoding mp3 and writing PCM wav — native.

This build has `--disable-libx264 --disable-libx265 --disable-libxvid
--disable-libxavs2 --disable-libdavs2 --disable-librubberband
--disable-libvidstab --disable-avisynth`, which is the list that would
otherwise force GPL. Run `ffmpeg -version` and check for `--enable-gpl`
before trusting any replacement: its absence is the whole point.

### What redistributing it still requires

LGPL v3 is lighter than GPL v3, not free of obligations. Invoking ffmpeg as a
**separate program over a subprocess** is aggregation rather than linking, so
animkit itself is unaffected by the licence in either direction. But when you
pass the binary on, you must:

1. **Include the licence text.** That is the `LICENSE` file beside this one,
   and `scripts/make_release.ps1` ships it whether or not the binary goes with
   it.
2. **Make the corresponding source available.** Unmodified upstream, so a
   pointer is enough:
   - source: <https://git.ffmpeg.org/ffmpeg.git>, commit `c573a95381`
   - the build recipe that produced this exact binary:
     <https://github.com/BtbN/FFmpeg-Builds>

Both are recorded here so that whoever cuts a release does not have to go
looking for them, and so a recipient who asks gets an answer rather than a
shrug.

## Replacing it

1. get a build for the platform — check `ffmpeg -version` for `--enable-gpl`
   and think about the paragraph above if it is there;
2. put its `ffmpeg` / `ffmpeg.exe` in the folder for that platform;
3. replace `LICENSE` with the one that build ships, and update the table above;
4. run `.\scripts\run_tests.ps1` — `tests/test_transcode.py` runs 32 real
   conversions against whatever binary is here, so it will tell you
   immediately if the build cannot do the job. It is the only test tier that
   exercises the binary itself rather than the code around it.

## Size, and opting out

126 MB dwarfs the ~500 KB of Python around it. That is the cost of the tool
working out of the box on a machine with nothing installed, and
`make_release.ps1` ships it by default for exactly that reason — a tester who
has to install a dependency before the reference feature works is a tester who
reports the reference feature as broken.

If that trade is wrong for your pipeline, `make_release.ps1 -NoFFmpeg` leaves
it out and the package drops to under a megabyte. Then either set
`reference.ffmpeg` to a shared one, or rely on `PATH`
(`winget install Gyan.FFmpeg`). Everything still works; only the "no setup
required" property is lost, and the installer says so at install time.

## macOS and Linux

The slots are empty because no build for them has been tested here. Dropping a
binary in is all that is required — the lookup is per platform and the code
path is identical. Note that a file copied to macOS usually needs `chmod +x`
and may need to be cleared of the quarantine attribute
(`xattr -d com.apple.quarantine ffmpeg`) before it will run.
