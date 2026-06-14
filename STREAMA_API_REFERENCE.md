# Streama API Reference

This document describes the Streama server HTTP API as used by the Streama VLC
Browser client. It was derived from the Streama 1.11.0 web client's `apiService`
and `playerService`, and verified against a live 1.11.0 server.

It is intended as a specification for building clients (this desktop app, a
future Android app, etc.).

---

## Conventions

- **Base URL:** `http(s)://<server>:<port>`
- **Auth:** session-cookie based. Logging in sets a `JSESSIONID` cookie which
  must be sent on every subsequent request. (A standard cookie-aware HTTP
  session handles this automatically.)
- **Profile scoping:** viewing history is per sub-profile. After login, the
  active profile's id must be sent as a `profileId` HTTP **header** on every
  request, or viewing-status endpoints return empty results.
- **Query parameters:** should be passed URL-encoded (as a params object),
  not hand-built into the URL, so values with spaces or special characters
  work correctly.
- **Responses:** JSON. List endpoints typically return
  `{ "total": <int>, "list": [ ... ] }`.
- **Session expiry:** on an expired session the server responds with 401
  (web client redirects to `/login/auth?sessionExpired=true`) or 403
  ("you do not have the rights"). Clients should detect these and route the
  user back to login.

---

## Authentication

### `POST /login/authenticate`
Authenticates a user and establishes the session cookie.

- **Body (form-encoded):** `username`, `password`, `remember_me`
- **Recommended headers:** `Accept: application/json, text/plain, */*`,
  `X-Requested-With: XMLHttpRequest`
- **Response:** JSON containing `success` (bool) and, on success, `username`.
  On failure, an `error` message.
- **Side effect:** sets the `JSESSIONID` cookie used for all later calls.

---

## Profiles

### `GET /profile/getUserProfiles.json`
Returns the list of sub-profiles belonging to the logged-in account.

- **Response:** a JSON array of profile objects, each with at least:
  - `id` — the profile id (send this as the `profileId` header)
  - `profileName` — display name
  - `isChild`, `isDeleted`, `profileLanguage`, `user: { id }`
- **Purpose:** present a "Who's watching?" selection. The chosen profile's
  `id` must be set as the `profileId` header on subsequent requests for
  Continue Watching to be scoped correctly.

---

## Dashboard / browsing

All of these accept paging via `max` and `offset` query parameters.

### `GET /dash/listContinueWatching.json?max={n}`
Returns the "Continue Watching" list for the active profile.

- **Requires:** the `profileId` header (else returns an empty list).
- **Response:** `{ "total": <int>, "list": [ <viewingStatus> ] }`, where each
  `viewingStatus` entry wraps the video and carries progress:
  - `id` — the viewing-status row id (not the video id)
  - `currentPlayTime` — seconds watched (resume point)
  - `runtime` — total duration in seconds
  - `lastUpdated`, `dateCreated` — ISO timestamps
  - `video` — the actual media object (`id`, `title`/`name`, poster fields,
    `videoFiles`, `subtitles`, etc.)
- **Notes:** the server stores a new row for every progress save, so the same
  video can appear many times. Clients should group by `video.id`, keep the
  row with the highest `currentPlayTime`, and sort by `lastUpdated`
  descending (newest first).

### `GET /dash/listMovies.json?max={n}&offset={o}`
Returns the movie library page.
- **Response:** `{ "total": <int>, "list": [ <movie> ] }`

### `GET /dash/listShows.json?max={n}&offset={o}`
Returns the TV-show library page.
- **Response:** `{ "total": <int>, "list": [ <tvShow> ] }`

### `GET /dash/listGenericVideos.json?max={n}&offset={o}`
Returns the "generic videos" library page (videos that are neither movies nor
TV shows).
- **Response:** `{ "total": <int>, "list": [ <video> ] }`

### `GET /dash/searchMedia.json?query={text}`
Full-text search across the library.
- **Parameter:** `query` (URL-encoded).
- **Response:** an object with `movies`, `shows`, and `genericVideos` arrays.

---

## Media details

### `GET /video/show.json?id={videoId}`
Returns full details for a single video (movie or generic video).
- **Response:** a media object including `videoFiles`, `subtitles`, `overview`,
  poster fields, etc.
- **Note:** this response does **not** include `currentPlayTime`; resume
  position must be carried from the Continue Watching entry separately.

### `GET /tvShow/show.json?id={showId}`
Returns details for a TV show (metadata, seasons).

### `GET /tvShow/episodesForTvShow.json?id={showId}`
Returns the list of episodes for a show.
- **Episode objects** may include playback-helper fields:
  `intro_start`, `intro_end` (skip-intro), and `outro_start` (next-episode
  prompt), in addition to the usual `videoFiles` and `subtitles`.

---

## Files (streaming and downloads)

### `GET /file/serve/{fileId}.{ext}`
Serves a video file (for streaming) or a subtitle/other file (for download).

- **Video stream:** use the `id` from a video's `videoFiles[0]` with extension
  `mp4` (or the file's actual type), e.g. `/file/serve/1106.mp4`.
- **Subtitle download:** use a subtitle entry's `id` with extension `srt`,
  e.g. `/file/serve/1105.srt`.
- **Auth:** the `JSESSIONID` cookie must be presented (as a cookie header, or
  appended as `;jsessionid=<id>` for players that need it inline).

---

## Viewing status (watch progress)

This is what populates the Continue Watching list. The web client calls these
while playing.

### `GET /viewingStatus/save.json?videoId={id}&currentTime={s}&runtime={total}`
Reports current playback position.

- **Parameters (all in SECONDS):**
  - `videoId` — the video's id
  - `currentTime` — current position in seconds
  - `runtime` — total duration in seconds
- **Cadence:** the web client sends this every 5 seconds during playback, and
  only when both `videoId` and `runtime` are present.
- **Note:** players reporting milliseconds (e.g. libVLC) must divide by 1000.

### `GET /viewingStatus/markCompleted.json?id={videoId}`
Marks a video as fully watched, removing it from Continue Watching.

- **Parameter:** `id` — the video id.
- **Typical use:** call when the user has watched to ~95% or more on exit,
  instead of saving a near-end position that would leave it stuck in the list.

---

## Metadata configuration

### `GET /theMovieDb/hasKey.json`
Returns TheMovieDB configuration, including whether an API key is set and the
image base URLs.

- **Response:** includes `key` and an `images.secure_base_url` used to build
  poster/backdrop URLs for TMDB-sourced artwork.

---

## Object field quick-reference

**Video / media object** (from list and detail endpoints):
- `id`, `title` or `name`, `overview`
- poster fields: `poster_image_src`, `poster_path`, `still_path`
- `mediaType` — e.g. `"tvShow"` for shows
- `videoFiles` — array; `videoFiles[0].id` is the stream file id
- `subtitles` — array of subtitle objects

**Subtitle object:**
- `id` — file id (download via `/file/serve/{id}.srt`)
- `language`, `label` — may be empty; used for menu display
- `originalFilename` — fallback display name

**ViewingStatus entry** (Continue Watching list items):
- `id` — status row id
- `currentPlayTime` — resume point in seconds
- `runtime` — total seconds
- `lastUpdated`, `dateCreated` — ISO timestamps
- `video` — the wrapped media object

---

## Minimal client flow

1. `POST /login/authenticate` → get `JSESSIONID`.
2. `GET /profile/getUserProfiles.json` → pick a profile → set `profileId`
   header for all later calls.
3. `GET /theMovieDb/hasKey.json` → image base URL for posters.
4. `GET /dash/listContinueWatching.json` (+ `listMovies` / `listShows` /
   `listGenericVideos`) to populate the dashboard.
5. On selecting an item: `GET /video/show.json` (or the tvShow endpoints) for
   `videoFiles` and `subtitles`.
6. Stream from `/file/serve/{fileId}.mp4`; download subtitles from
   `/file/serve/{subId}.srt`.
7. While playing: `GET /viewingStatus/save.json` every 5 seconds; on exit,
   a final save or `markCompleted`.
