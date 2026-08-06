# fetch.subtitles

Bulk subtitle fetcher for a local TV series library, built on
[subliminal](https://github.com/Diaoul/subliminal).

Takes one or more directories and/or specific video files, and searches
every video found for subtitles, regardless of whether one already exists —
matching by series/season/episode parsed from the filename (via guessit),
not by video hash. Tries a first tier of name/metadata-based providers
(gestdown, podnapisi, tvsubtitles), then falls back to a second tier
(opensubtitles, bsplayer, getsubtitle.com) for anything still missing.
Existing subtitle files are never overwritten — a freshly downloaded one
gets a unique filename instead.

## Usage

Docker (published as [`svemirzeka/fetch-subtitles`](https://hub.docker.com/r/svemirzeka/fetch-subtitles)):

```
docker run --rm -v /path/to/library:/videos svemirzeka/fetch-subtitles -l en -l sr
```

Or locally:

```
pip install -r requirements.txt
python fetch_subtitles.py [PATH ...] [-l en] [-l sr] [--dry-run]
```

Each `PATH` is either a directory (scanned recursively, so it works whether
videos live directly in it or in per-season subfolders like S01, S02, ...)
or a specific video file — mix and match, repeatable. Defaults to the
current directory if omitted.

### Options

- `-l, --language` — Language code (IETF, e.g. `en`, `sr`). Repeatable.
  Default: `en`.
- `--dry-run` — Only list the videos that would be searched, don't download.
- `--force-<provider>` — Use only that provider, skipping the others and
  the tier 1/tier 2 split. One flag per provider: `--force-gestdown`,
  `--force-podnapisi`, `--force-tvsubtitles`, `--force-opensubtitles`,
  `--force-bsplayer`, `--force-getsubtitle`. Combine multiple flags to use
  exactly that set (e.g. `--force-bsplayer --force-getsubtitle` to use only
  the two hash-based providers).
- `--debug` — Show full tracebacks for provider failures instead of
  one-line summaries, for troubleshooting a specific provider.
- `--all-versions` — Download every matching subtitle found instead of just
  the best one, saved as `<video>.<lang>.<index>.srt`. Searches all
  providers in a single pass rather than tier by tier.
- `--version` — Show the script version.

If only one language is requested, the saved subtitle filename matches the
video filename exactly (extension only). With multiple languages, each
subtitle gets a language-code suffix (e.g. `.en.srt`, `.sr.srt`). With
`--all-versions`, every match gets its own numbered file instead (e.g.
`.en.1.srt`, `.en.2.srt`). Since every run searches regardless of what's
already there, a name that's already taken gets a numeric suffix instead
of being overwritten (e.g. a second `en` download becomes `.en.2.srt`).

## Versioning

This project follows [Semantic Versioning](https://semver.org/). See
[CHANGELOG.md](CHANGELOG.md) for release history.
