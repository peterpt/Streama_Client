# Changelog — Streama VLC Browser

This release fixes the "Continue Watching" feature end to end and adds
resume-from-last-position, profile selection, and Netflix-style in-player
subtitle and audio track switching.

All changes are in three files: `api.py`, `player.py`, `ui_widgets.py`,
and `streama-client.py`.

---

## Major features

### Continue Watching now works (read + write)

Previously the app fetched the Continue Watching list but never reported
playback progress back to the server, so the list was always empty.

- The player now reports progress to the server every 5 seconds during
  playback via `viewingStatus/save.json`, matching the official web client's
  behaviour and cadence.
- On exit, a final progress save is sent. If the video was watched to 95% or
  more, it is marked completed (`viewingStatus/markCompleted.json`) so it
  leaves the Continue Watching list instead of lingering near the end.
- Progress saves run on a background thread so a slow or unreachable server
  never stutters playback or freezes the UI.

### Profile selection at login

Streama scopes viewing history per sub-profile. The app now loads the
account's profiles after login and shows a "Who's watching?" selection dialog
on every login (matching the web client). The chosen profile's id is sent as a
`profileId` header on every request, which is what makes Continue Watching
return the correct per-profile history. Without this header the server returns
an empty list. The selection is per-session and is not saved to disk.

### Resume from last watched position

Selecting an item from Continue Watching now resumes from where it was left
off instead of starting from the beginning.

- The resume position (`currentPlayTime`, in seconds) is carried from the
  Continue Watching item through the details fetch (which would otherwise
  discard it) into the player.
- The seek is applied only once VLC reports the stream is actually seekable,
  retrying for up to ~10 seconds. This is reliable for large MP4 files
  streamed over HTTP, where a fixed delay was not.

### Netflix-style in-player subtitle and audio track switching

- All of a video's subtitles are downloaded and loaded into VLC as switchable
  tracks, so the user can change subtitle language during playback with no
  re-download and no restart.
- A "Subs" and an "Audio" button were added to the player control bar, and
  Subtitles/Audio submenus were added to the right-click menu. Both let the
  user switch tracks (including turning subtitles off) instantly.
- Audio track switching is read directly from the media file, so multi-audio
  releases get a track picker.

### Smarter pre-play subtitle selection

The pre-play subtitle selection screen now only appears when there is an
actual choice to make:

- **Multiple subtitles:** the selection screen is shown; the chosen language
  is enabled when playback starts.
- **One subtitle:** the selection screen is skipped; the subtitle loads and
  is enabled automatically.
- **No subtitles:** the selection screen is skipped; playback starts with no
  subtitle.

In all cases, subtitles can still be changed from inside the player.

---

## Fixes and robustness

- **Continue Watching response parsing.** The endpoint returns viewing-status
  objects wrapped in a `{ "total": N, "list": [...] }` structure, where each
  entry wraps the actual video. The app now unwraps this correctly instead of
  discarding it (which previously caused an "Expected list, got dict" warning
  and an empty list).

- **Correct ordering.** The Continue Watching list is now sorted most-recently
  watched first (by `lastUpdated`), matching the web dashboard.

- **De-duplication and correct resume point.** The server writes a new status
  row for every 5-second save, so a single watch produces many rows for the
  same video sharing one timestamp. The app now groups rows by video, keeps
  the row with the furthest progress (so resume lands where the user actually
  stopped, not the start of the session), and shows one tile per video.

- **URL-encoded search.** Search now passes its query as a parameter object
  rather than building the URL by hand, so titles with spaces or special
  characters no longer break the request.

- **Subtitle temp-file cleanup.** Temporary subtitle files (now potentially
  several per video) are tracked and removed on exit and between playbacks.

---

## Notes for users with different libraries

The subtitle handling is built for the general case, so it adapts to whatever
a given video has on the server:

- Videos with several language subtitles show a full language picker.
- Videos with a single subtitle play immediately with it enabled.
- Videos with no subtitles (e.g. already in the viewer's language) play with
  no subtitle and no prompt.

Subtitle menu labels come from the server's `language` / `label` fields when
present, falling back to the filename. Filling in the language field on the
server gives the cleanest menu labels.

---

## Known server-side behaviour (not a client bug)

- The same movie can appear multiple times in the raw Continue Watching data
  because the server stores many progress rows per video. The client now
  de-duplicates these into a single tile.
