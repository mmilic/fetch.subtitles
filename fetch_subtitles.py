#!/usr/bin/env python3
"""
Bulk subtitle fetcher for a local TV series library.

Scans season folders for video files, skips any that already have subtitles
in every target language, and fetches the rest using subliminal - matching by
series/season/episode parsed from the filename (via guessit), not by video
hash. Some providers only support hash-based lookup or may be unreachable in
a given environment; this script tries a first tier of name/metadata-based
providers, then falls back to a second tier for anything still missing.

Usage:
    pip install subliminal
    python fetch_subtitles.py [ROOT_DIR] [-l en] [-l sr] [--dry-run]

ROOT_DIR defaults to the current directory. It is scanned recursively, so it
works whether videos live directly in ROOT_DIR or in per-season subfolders
(S01, S02, ...).
"""

__version__ = "0.1.0"

import argparse
import logging
from pathlib import Path

from babelfish import Language
from dogpile.cache.exception import RegionAlreadyConfigured
from subliminal import scan_video, download_best_subtitles, save_subtitles
from subliminal.cache import region as subliminal_cache_region
from subliminal.core import search_external_subtitles

logging.basicConfig(level=logging.WARNING)
logger = logging.getLogger("fetch_subtitles")

# subliminal logs an ERROR (with traceback) whenever it can't guess a language
# from a subtitle filename suffix (e.g. "...-eng(1).srt" from a prior manual
# download) - that's expected noise from search_external_subtitles, not
# something this script needs to react to.
logging.getLogger("subliminal.subtitle").setLevel(logging.CRITICAL)

VIDEO_EXTENSIONS = {".mkv", ".mp4", ".avi"}

# Tier 1: search by series/season/episode text query, no video hash needed.
PROVIDERS_BY_NAME = ["gestdown", "podnapisi", "tvsubtitles"]

# Tier 2 fallback: opensubtitles also supports a metadata-only query
# (series + season + episode), used here purely as a fallback source -
# not for its hash-based lookup.
PROVIDERS_FALLBACK = ["opensubtitles"]

# Some providers (e.g. tvsubtitles) cache show/episode lookups via
# subliminal's dogpile region, which raises RegionNotConfigured if left
# unconfigured - silently killing that provider on every call. An in-memory
# backend is enough: it still dedupes repeat show lookups within a single
# run (e.g. every episode of the same series), and unlike dogpile's dbm
# backend it doesn't depend on fcntl, which isn't available on Windows.
def configure_cache() -> None:
    try:
        subliminal_cache_region.configure("dogpile.cache.memory")
    except RegionAlreadyConfigured:
        pass


def find_videos(root: Path) -> list[Path]:
    return sorted(
        p for p in root.rglob("*")
        if p.is_file() and p.suffix.lower() in VIDEO_EXTENSIONS
    )


def missing_languages(video_path: Path, languages: set[Language]) -> set[Language]:
    """Return the subset of `languages` not already covered by a subtitle
    next to `video_path`."""
    existing = search_external_subtitles(video_path.name, directory=str(video_path.parent))
    have = {sub.language for sub in existing.values()}
    return languages - have


def fetch_tier(needed: dict[Path, set[Language]], providers: list[str], single: bool) -> dict[Path, set[Language]]:
    """Attempt to download subtitles for the videos in `needed` (a path ->
    still-required-languages mapping) using `providers`. Returns a mapping
    of the same shape for whatever languages are still missing afterwards."""
    if not needed:
        return {}

    scanned = {}
    still_needed = {}
    for path, langs in needed.items():
        try:
            scanned[path] = scan_video(str(path))
        except Exception as exc:
            logger.warning("Could not scan %s: %s", path, exc)
            still_needed[path] = langs

    if not scanned:
        return still_needed

    all_languages = set().union(*(needed[path] for path in scanned))
    subtitles = download_best_subtitles(set(scanned.values()), all_languages, providers=providers)

    for path, video in scanned.items():
        found = subtitles.get(video, [])
        if found:
            save_subtitles(video, found, single=single)
        remaining = needed[path] - {sub.language for sub in found}
        if not remaining:
            print(f"  OK       {path}")
        else:
            found_str = " (partial)" if found else ""
            print(f"  MISSING{found_str}  {path}")
            still_needed[path] = remaining

    return still_needed


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    parser.add_argument("root", nargs="?", default=".", help="Directory to scan (default: current directory)")
    parser.add_argument("-l", "--language", action="append", dest="languages",
                         default=None, help="Language code (IETF, e.g. en, sr). Repeatable. Default: en")
    parser.add_argument("--dry-run", action="store_true", help="Only report what's missing, don't download")
    args = parser.parse_args()

    configure_cache()

    root = Path(args.root).resolve()
    lang_codes = args.languages or ["en"]
    languages = {Language.fromietf(code) for code in lang_codes}
    single = len(languages) == 1

    print(f"Scanning {root} for video files...")
    videos = find_videos(root)
    print(f"{len(videos)} video files found.")

    needed = {}
    for v in videos:
        langs = missing_languages(v, languages)
        if langs:
            needed[v] = langs
    print(f"{len(needed)} missing a subtitle in {', '.join(lang_codes)}.")

    if args.dry_run:
        for v, langs in needed.items():
            lang_str = ", ".join(sorted(str(lang) for lang in langs))
            print(f"  MISSING  {v}  [{lang_str}]")
        return

    if not needed:
        print("Nothing to do.")
        return

    print(f"\nTier 1 ({', '.join(PROVIDERS_BY_NAME)}) - name/metadata-based matching:")
    still_needed = fetch_tier(needed, PROVIDERS_BY_NAME, single)

    if still_needed:
        print(f"\nTier 2 fallback ({', '.join(PROVIDERS_FALLBACK)}) for {len(still_needed)} remaining:")
        still_needed = fetch_tier(still_needed, PROVIDERS_FALLBACK, single)

    print(f"\nDone. {len(videos) - len(still_needed)}/{len(videos)} videos now have subtitles in all requested languages.")
    if still_needed:
        print(f"{len(still_needed)} still missing (no match found by any provider):")
        for v, langs in still_needed.items():
            lang_str = ", ".join(sorted(str(lang) for lang in langs))
            print(f"  NO MATCH  {v}  [{lang_str}]")


if __name__ == "__main__":
    main()
