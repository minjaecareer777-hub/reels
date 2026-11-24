# Europe life reel — Switzerland × Finland

A 20.6 s vertical reel (1080×1920, 30 fps) for Korean and Japanese viewers.

**Idea.** A Tuesday, 07:00, on a packed commuter train: "what is 7 o'clock like over there?" A globe turns from Seoul / Tokyo to Europe, then one ordinary day runs from 07:00 to 23:00 — a Swiss morning and afternoon, a Finnish evening, the aurora at night. It ends on "what if this weren't a trip, just a Tuesday" and points quietly at the profile link.

| File | Purpose |
|---|---|
| `out/europe_life_reel.mp4` | The finished reel (upload this) |
| `out/cover.jpg` | Cover frame for the grid |
| `caption.md` | Post captions (KR / JP), hashtags, photo credit line, pre-publish checks |
| `CREDITS.md` | Source and licence of every clip, photo, texture, track and font |
| `timeline.json` | All copy, cuts, crops and timing — edit this to change the reel |
| `make_reel.py`, `globe.py` | Renderer (Python + ffmpeg) |
| `assets_manifest.json`, `fetch_assets.py` | Where the source media comes from, and a script that downloads it |

## Structure

| Time | Section | On screen |
|---|---|---|
| 0.0–3.0 s | Hook | Globe from East Asia to Europe; "화요일 아침 7시, 오늘도 지옥철 안 / 火曜の朝7時、今日も満員電車に揺られてる", then "거기 7시는 어떨까 / 向こうの朝7時は" |
| 3.0–16.7 s | Montage | 16 cuts, one every two beats (0.857 s), each stamped with a clock time, flag and place; five bilingual lines, each held for three cuts |
| 16.7–20.6 s | Ending | Aurora over Lapland; "여행이 아니라 그냥 화요일이라면" and the profile-link line |

Every place label is the real location of that shot (see `CREDITS.md`).

## Re-rendering

```bash
pip install numpy pillow opencv-python-headless   # plus ffmpeg, curl, npm on PATH
python3 fetch_assets.py          # downloads media + fonts into ./assets (about 150 MB)
python3 make_reel.py --stills    # one check frame per scene -> out/stills/
python3 make_reel.py             # full render -> out/europe_life_reel.mp4 (about 6 min)
```

Emoji in copy are drawn with Noto Color Emoji (`/usr/share/fonts/truetype/noto/NotoColorEmoji.ttf`); the current copy uses none.

### Changing things in `timeline.json`

- **Copy**: `hook`, `subtitles[]`, `ending` hold the Korean (`ko`) and Japanese (`ja`) text; `\n` breaks a line.
- **A cut**: each entry in `cuts[]` has `time`, `place`, `country` (`CH` / `FI`), `src`, the crop centre `cx` / `cy` (0–1) and the `zoom` start/end. Videos also take `start` (seconds).
- **Pace**: `cut_period` is two beats of the music (0.857 s at 140 BPM); `music.start` is chosen so the beat comes in exactly when the hook ends.
- **Your own footage**: drop a vertical clip into `assets/clips/` and point a cut's `src` at it with `"kind": "video"`.

## Music

The reel ships with "Opalescent" by Eugenio Mininni (Mixkit Stock Music Free License). To use a trending Instagram sound instead, mute the original audio in the Reels editor and add the sound there; cuts fall every 0.857 s starting at 3.0 s.
