# Changelog

All notable changes to this project are documented in this file.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/).

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
