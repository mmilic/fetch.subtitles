# fetch.subtitles

Bulk subtitle fetcher for a local TV series library, built on
[subliminal](https://github.com/Diaoul/subliminal).

Scans a directory recursively for video files, skips any that already have
subtitles in every target language, and fetches the rest — matching by
series/season/episode parsed from the filename (via guessit), not by video
hash. Tries a first tier of name/metadata-based providers (gestdown,
podnapisi, tvsubtitles), then falls back to a second tier (opensubtitles,
bsplayer, getsubtitle.com) for anything still missing.

## Usage

Docker (published as [`svemirzeka/fetch-subtitles`](https://hub.docker.com/r/svemirzeka/fetch-subtitles)):

```
docker run --rm -v /path/to/library:/videos svemirzeka/fetch-subtitles -l en -l sr
```

Or locally:

```
pip install -r requirements.txt
python fetch_subtitles.py [ROOT_DIR] [-l en] [-l sr] [--dry-run]
```

`ROOT_DIR` defaults to the current directory and is scanned recursively, so
it works whether videos live directly in `ROOT_DIR` or in per-season
subfolders (S01, S02, ...).

### Options

- `-l, --language` — Language code (IETF, e.g. `en`, `sr`). Repeatable.
  Default: `en`.
- `--dry-run` — Only report what's missing, don't download.
- `--force-bsplayer` — Skip the name/metadata-based providers and use only
  bsplayer's hash-based lookup, which matches your exact release instead of
  guessing by series/season/episode.
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
`.en.1.srt`, `.en.2.srt`).

## Versioning

This project follows [Semantic Versioning](https://semver.org/). See
[CHANGELOG.md](CHANGELOG.md) for release history.
