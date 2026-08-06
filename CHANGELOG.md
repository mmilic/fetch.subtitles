# Changelog

All notable changes to this project are documented in this file.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/).

## [0.9.0] - 2026-08-06

### Added
- The positional argument now accepts one or more paths, each a directory
  (scanned recursively as before) or a specific video file, in any mix.
  Lets you target just a handful of episodes instead of a whole directory.
- The `fetch-subtitles` shell alias now translates host filenames passed
  on the command line into the corresponding path inside the container's
  `/videos` mount, rather than only supporting a single directory argument.

### Changed
- `find_videos()` now takes a list of targets instead of a single root
  directory.

## [0.8.0] - 2026-08-06

### Changed
- Removed the pre-check that skipped a video if it already had a subtitle
  for every requested language. Every run now searches and downloads for
  every video regardless of what's already there. Existing subtitle files
  are never overwritten - a freshly downloaded one that would collide with
  an existing filename gets a numeric suffix instead (e.g. `.en.2.srt`).
  `--all-versions` continues numbering from whatever's already on disk
  instead of restarting at 1, so repeated runs accumulate rather than
  clobber earlier results.
- As a side effect, this removes the 0.3.2 bug where a suffix-less
  single-language subtitle (detected by subliminal as language "und") was
  trusted to cover *any* single language requested - there's no longer a
  "does it already cover this" check to get that wrong.
- `--dry-run` and its help text now describe listing videos that would be
  searched, rather than videos "missing" a subtitle - accurate now that
  every video is always searched.

### Removed
- `missing_languages()` and the `search_external_subtitles`-based existing-
  subtitle detection it relied on.

## [0.7.0] - 2026-08-06

### Added
- `--force-<provider>` flag for every provider (`--force-gestdown`,
  `--force-podnapisi`, `--force-tvsubtitles`, `--force-opensubtitles`,
  `--force-bsplayer`, `--force-getsubtitle`), generalizing the existing
  `--force-bsplayer`. Combinable to select an exact custom provider set,
  bypassing the tier 1/tier 2 split; unchanged when none are passed.

## [0.6.0] - 2026-08-06

### Added
- `--all-versions` flag to download every matching subtitle found for a
  video/language instead of just the single best match, saved as
  `<video>.<lang>.<index>.srt`. Searches all providers in one combined
  pass instead of tier by tier, since the whole point is exhaustive
  results rather than stopping early. Verified end-to-end: 54 distinct
  real subtitle files saved for a single test episode.

## [0.5.0] - 2026-08-06

### Added
- `getsubtitle.com` added to the tier-2 fallback providers, as a custom
  provider (not bundled with subliminal). Its own website is a dead stub,
  but its SOAP API at api.getsubtitle.com is still live and was verified by
  hand (search, download, and real subtitle content all confirmed against
  a real video hash). Hash-based only, like bsplayer, and reuses the same
  hash algorithm.

## [0.4.0] - 2026-08-06

### Added
- `--debug` flag to show full tracebacks for provider failures instead of
  the one-line summaries introduced in 0.3.1, for troubleshooting a
  specific provider.

## [0.3.2] - 2026-08-06

### Fixed
- Fixed a regression from the 0.1.0 single-language filename change: a
  subtitle saved without a language suffix (e.g. `Show S01E01.srt`) was
  detected by subliminal as language `und` (undefined), not the language it
  actually was, so every run treated it as missing and re-downloaded it. In
  single-language mode, an existing `und` subtitle is now trusted to already
  cover the requested language.

## [0.3.1] - 2026-08-06

### Changed
- subliminal's per-provider failure logs (timeouts, HTTP errors, rate
  limiting, etc.) no longer dump a full traceback to the terminal. It
  already handles these gracefully by falling back to the next provider or
  tier, so only the one-line message is kept.

## [0.3.0] - 2026-08-06

### Added
- `--force-bsplayer` flag to skip the name/metadata-based tiers and use only
  bsplayer's hash-based lookup, which matches your exact release instead of
  guessing by series/season/episode.

## [0.2.0] - 2026-08-06

### Added
- `bsplayer` added to the tier-2 fallback providers (bsplayer-subtitles.com).
  Unlike the other providers, it only supports hash-based lookup, so the
  video file's hash is now computed and injected before querying it.

## [0.1.0] - 2026-08-06

### Added
- Initial release: bulk subtitle fetcher for a local TV series library, built
  on subliminal. Scans a directory recursively, matches videos by
  series/season/episode (via guessit), and fetches missing subtitles using a
  two-tier provider strategy (gestdown/podnapisi/tvsubtitles, falling back to
  opensubtitles).
- `--dry-run` flag to report what's missing without downloading.
- `-l/--language` flag (repeatable) to request one or more subtitle
  languages.
- `--version` flag.
- Dockerfile and published image (`svemirzeka/fetch-subtitles`).

### Changed
- When only one language is requested, the saved subtitle filename now
  matches the video filename exactly (extension only), instead of including
  a language-code suffix.
