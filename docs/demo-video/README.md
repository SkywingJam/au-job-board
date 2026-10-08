# 30-second product demo video

| File | What |
|---|---|
| [`product-demo-30s.mp4`](product-demo-30s.mp4) | Final cut: 1920x1080, 30 fps, 900 frames, 30.0 s, H.264 High / yuv420p (BT.709, limited range) + AAC 48 kHz stereo, CRF 18, ~7.3 MB |
| [`cover.png`](cover.png) | Cover / poster frame, 1920x1080 |
| [`banner-en.png`](banner-en.png) · [`banner-zh.png`](banner-zh.png) | Banners, 1600x800, English / Chinese (same design, only title and short copy differ) |
| [`script.md`](script.md) | Creative approach, script, storyboard, timing and captions (archived; no approval round) |

## Public description (safe to reuse)

> A 30-second walkthrough of a personal job-search board. Listings from several
> job boards land in one ranked list with duplicates merged; hard deal-breakers
> are flagged with the original sentence quoted as evidence; each job is labelled
> and noted in a few clicks; and the priority filter turns the labelled jobs into an
> apply queue. All data shown is synthetic.

The video, captions, cover and this description contain no repository name,
repository address or release information. The product title **AU Job Board**
appears in the UI header, outro and cover (cleared for display). Job links are never
opened, and a headless browser has no address bar or tab strip, so no URL appears.

## How it was made

- **Real UI, normal entry.** A fresh synthetic mock root is built with the existing
  `tools/prepare_screenshot_mock.py` (same hand-written inputs as the screenshot
  library: `demo/demo.jobs.json` + `docs/demo-screenshots/mock-inputs/`) and served by
  the ordinary `jobs.cli panel` on `127.0.0.1`. No Demo mode, no banner.
- **Automated operation and recording.** `tools/capture_demo_video.py` drives the
  panel with Playwright (Brave 154 headless, run at device scale 4/3, so a 1440x810
  CSS viewport gives 1920x1080 frames) and records each take with the Chrome DevTools
  screencast. Variable-rate frames are resampled to a constant 30 fps by timestamp.
- **Pointer from the live DOM.** Each target's position is measured with
  `bounding_box()` right before the move. The real mouse then travels a curved,
  eased 60 Hz path that triggers the page's real hover states. Every sample, press and
  keystroke is logged against the same clock as the frames. Headless Chrome draws no
  pointer, so the video draws one from that log. Click ripples and a soft tick sit at
  the logged press times.
- **Calibration.** For every click the script measures how long the page pixels took
  to change after the press (shown below). The stable 6-144 ms values, and a manual
  frame check at click frames, confirm that frames and actions share one timeline.
- **Framing.** The 1440x810 page sits in a 1600x900 rounded window. Zooms are
  camera moves onto boxes measured from the DOM (detail header, exclusion evidence,
  label panel). The pointer and ripples live inside the same transform, so their
  coordinates stay correct under zoom and crop, and the camera is clamped to the page
  edges. Captions sit in a band below the window and never cover the UI.
- **Composited, not product UI:** the intro (three pain points), outro and cover
  text, the step chips and captions, the drawn pointer, ripples and camera zoom.
  Every frame inside the window is real panel footage. There are no static-screenshot
  swaps.
- **Audio.** Music and tick are synthesised by `media/demo-video/scripts/make_audio.py`
  (numpy; pad + pluck arpeggio + bass, 100 BPM). No samples, no voice-over.

Measured click-to-first-visible-change (final capture): open merged job 11 ms ·
Excluded tab 117 · open excluded job 19 · open job 17 · Eligible 100 · Want 98 ·
Saved 99 · note field 31 · Prioritize 6 · Labelled tab 126 · Priority only 122.
No scripted step ran late.

## Banners

One editable source, `media/demo-video/src/Banner.tsx`
(stills `BannerEN` / `BannerZH`; the copy lives in its `COPY` table). That source
is part of the development-only Remotion project and is not distributed in the
public candidate. Layout: on the left, a
three-line title (last line in the accent colour) and a two-line value line; on the right,
a real panel screenshot in a 12 px-radius card that bleeds off the right and bottom edges.
Uses the panel's paper / ink / accent tokens and its font stack (`-apple-system`,
`PingFang SC`, …).

| | Title | Short copy |
|---|---|---|
| EN | Collect. Screen. **Follow up.** | Several job boards, one clear list. |
| ZH | 集中岗位 / 快速筛选 / **清晰跟进** | 多个招聘平台，一个清晰列表。 |

- Screenshot: the committed library image
  `docs/demo-screenshots/images/C-detail-labelled-en-light-desktop-1440x1000.png` (real UI,
  normal panel, synthetic data), imported directly by the source and shown at 0.72x. Both
  banners use the same image, so only the copy differs (the panel UI in it is English).
- Phone check: downscaled to 390 and 360 px wide (the width of a full-bleed phone
  image), the title stays bold and clear and the 48 px value line renders at ~12 px. The
  screenshot reads as product texture at that size.
- No repository name, address or release information. The UI header shows "AU Job Board",
  as in the video.

The two banners were rendered from that source with Remotion. The render
commands are not part of the public candidate; the committed PNGs above are
the result. The banner source and the Remotion project are development-only
and are not distributed.

## How it was produced (historical)

The video was captured from a fresh synthetic mock root served by the ordinary
panel, then rendered with Remotion from the development-only project under
`media/demo-video/`. The mock builder, capture tools and Remotion project named
above are maintainer tools: they are **not** part of the public candidate and no
reproduction commands are shipped here. The finished video, cover and banners in
this directory, together with the source and version notes above, are the record
of how they were made.

Raw frames, the mock DB, `takes.json` and audio stayed under a gitignored `out/`
task directory during production. A re-capture would give the same cut but not
byte-identical frames (screencast timing varies by a few ms; listing dates follow
the capture day).

## Versions

- Source code shown: `97f0ab62db801a3e0a14172eec84e0ad968ab6d3` (product code unchanged
  on this branch); mock data generated 2026-10-07.
- Remotion 4.0.533 (`@remotion/cli`, `remotion`, `@remotion/media`), React 19.2.3, with the
  official Remotion Agent Skills 4.0.533 (`npx skills add remotion-dev/skills`, kept in
  `out/` for reference, not committed).
- Playwright 1.63.0 (Python), Pillow 12.3.0, numpy 2.5.3, Brave 154.0.8037.98, Node 24.5.0.

## Limits

- The pointer is drawn from the logged real mouse path, because headless browsers draw
  none. The path and the click times are real; the arrow's look is not the OS cursor.
- Light theme, English UI and the desktop layout only. Timestamps such as
  "Posted 2026-10-05T09:00:00" are the panel's own formatting of synthetic dates.
- The note picked in the video is the default "Prioritize" suggestion. It is stored as
  the canonical tag, which is what the "Priority only" filter reads (see Limits in
  [`../demo.md`](../demo.md)). Typed English notes would not feed that filter.
- Remotion is free for individuals and teams of up to three; larger organisations need
  a company licence.
