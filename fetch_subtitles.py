#!/usr/bin/env python3
"""
Bulk subtitle fetcher for a local TV series library.

Takes one or more directories and/or specific video files, and searches
every video found for subtitles using subliminal - matching by
series/season/episode parsed from the filename (via guessit), not by video
hash - regardless of whether a subtitle already exists next to the video.
Existing subtitle files are never overwritten: a freshly downloaded one gets
a unique filename instead. Some providers only support hash-based lookup or
may be unreachable in a given environment; this script tries a first tier of
name/metadata-based providers, then falls back to a second tier for
anything still missing.

Usage:
    pip install subliminal
    python fetch_subtitles.py [PATH ...] [-l en] [-l sr] [--dry-run]

Each PATH is either a directory (scanned recursively, so it works whether
videos live directly in it or in per-season subfolders like S01, S02, ...)
or a specific video file. Defaults to the current directory if omitted.
"""

__version__ = "0.9.0"

import argparse
import base64
import logging
import zlib
from pathlib import Path

from babelfish import Language, language_converters
from defusedxml import ElementTree
from dogpile.cache.exception import RegionAlreadyConfigured
from requests import Session
from subliminal import scan_video, download_best_subtitles, download_subtitles, list_subtitles
from subliminal.cache import region as subliminal_cache_region
from subliminal.extensions import provider_manager
from subliminal.providers import Provider
from subliminal.providers.bsplayer import BSPlayerProvider
from subliminal.subtitle import Subtitle

logging.basicConfig(level=logging.WARNING)
logger = logging.getLogger("fetch_subtitles")

# subliminal logs an ERROR (with traceback) whenever it can't guess a language
# from a subtitle filename suffix (e.g. "...-eng(1).srt" from a prior manual
# download) - that's expected noise from search_external_subtitles, not
# something this script needs to react to.
logging.getLogger("subliminal.subtitle").setLevel(logging.CRITICAL)


class _QuietSubliminalTracebacks(logging.Filter):
    """subliminal logs a full traceback at ERROR level for every provider
    failure (timeouts, HTTP errors, rate limiting, ...) even though it
    already handles them gracefully - falling back to the next provider or
    tier. The one-line message (which provider, why) is useful; the
    traceback repeated per retry per video is not. Drop just the traceback."""

    def filter(self, record: logging.LogRecord) -> bool:
        if record.name.startswith("subliminal."):
            record.exc_info = None
            record.exc_text = None
        return True


class GetSubtitleSubtitle(Subtitle):
    """getsubtitle.com Subtitle."""

    provider_name = "getsubtitle"

    def __init__(self, language, subtitle_id, *, movie_hash=None, filename=""):
        super().__init__(language, subtitle_id)
        self.movie_hash = movie_hash
        self.filename = filename

    @property
    def info(self):
        return self.filename or self.subtitle_id

    def get_matches(self, video):
        return {"hash"}


class GetSubtitleProvider(Provider):
    """getsubtitle.com Provider.

    Not in subliminal's bundled provider list - www.getsubtitle.com's own
    site is a dead stub, but its SOAP API at api.getsubtitle.com is still
    live (confirmed by hand: getLanguages, searchSubtitlesByHash and
    downloadSubtitles all return real data). Only supports hash-based
    lookup, using the same OpenSubtitles-style hash bsplayer uses (see
    hash_video below) - confirmed by testing a real hash against the API.
    """

    languages = {Language.fromalpha3b(lang) for lang in language_converters["alpha3b"].codes}
    required_hash = "getsubtitle"

    api_url = "https://api.getsubtitle.com:443/server.php"
    namespace = "api.getsubtitle.com/nusoap"

    def __init__(self, timeout=10):
        self.timeout = timeout
        self.session = None

    def initialize(self):
        self.session = Session()

    def terminate(self):
        self.session.close()
        self.session = None

    def _request(self, func_name, body_xml):
        envelope = (
            '<?xml version="1.0" encoding="UTF-8"?>'
            '<SOAP-ENV:Envelope xmlns:SOAP-ENV="http://schemas.xmlsoap.org/soap/envelope/" '
            'xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" '
            'xmlns:xsd="http://www.w3.org/2001/XMLSchema" '
            'xmlns:SOAP-ENC="http://schemas.xmlsoap.org/soap/encoding/">'
            "<SOAP-ENV:Body>"
            f'<{func_name} xmlns="{self.namespace}">{body_xml}</{func_name}>'
            "</SOAP-ENV:Body>"
            "</SOAP-ENV:Envelope>"
        )
        headers = {
            "Content-Type": "text/xml; charset=utf-8",
            "SOAPAction": f'"{self.namespace}#{func_name}"',
            "User-Agent": "Mozilla/5.0",
        }
        res = self.session.post(self.api_url, data=envelope.encode("utf-8"), headers=headers, timeout=self.timeout)
        return ElementTree.fromstring(res.content)

    def query(self, language, file_hash):
        root = self._request(
            "searchSubtitlesByHash",
            f'<hash xsi:type="xsd:string">{file_hash}</hash>'
            f'<language xsi:type="xsd:string">{language.alpha3}</language>'
            '<index xsi:type="xsd:int">0</index>'
            '<count xsi:type="xsd:int">10</count>',
        )
        subtitles = []
        for item in root.findall(".//item"):
            cod = item.findtext("cod_subtitle_file")
            if not cod:
                continue
            subtitles.append(
                GetSubtitleSubtitle(
                    language=language,
                    subtitle_id=cod,
                    movie_hash=file_hash,
                    filename=item.findtext("file_name", default=""),
                )
            )
        return subtitles

    def list_subtitles(self, video, languages):
        file_hash = video.hashes.get("getsubtitle")
        if not file_hash:
            return []
        subtitles = []
        for language in languages:
            subtitles.extend(self.query(language, file_hash))
        return subtitles

    def download_subtitle(self, subtitle):
        root = self._request(
            "downloadSubtitles",
            '<subtitles SOAP-ENC:arrayType="tns:SubtitleDownload[1]" xsi:type="SOAP-ENC:Array">'
            '<item xsi:type="tns:SubtitleDownload">'
            f"<movie_hash xsi:type=\"xsd:string\">{subtitle.movie_hash}</movie_hash>"
            f"<cod_subtitle_file xsi:type=\"xsd:int\">{subtitle.subtitle_id}</cod_subtitle_file>"
            "</item>"
            "</subtitles>",
        )
        data = root.findtext(".//data")
        if not data:
            return
        subtitle.set_content(zlib.decompress(base64.b64decode(data)))


# getsubtitle.com uses the same hash algorithm as bsplayer (confirmed by
# testing a real hash against the API) - reuse it rather than duplicate it.
GetSubtitleProvider.hash_video = staticmethod(BSPlayerProvider.hash_video)

# This module doubles as the __main__ script, so it can't be registered
# under its own dotted import path (subliminal would try to re-import
# "fetch_subtitles" as a *separate* module from the running __main__ one,
# recursively re-executing this whole file). Registering it against
# __main__ instead points at the module that's already fully loaded in
# sys.modules, so no re-import ever happens - but only holds when this file
# itself is what got run as __main__ (e.g. `python fetch_subtitles.py`).
# Imported some other way (a REPL, a test harness), __main__ is whatever
# that context's entry point is and won't have GetSubtitleProvider on it -
# in which case the getsubtitle provider just isn't registered.
try:
    provider_manager.register("getsubtitle = __main__:GetSubtitleProvider")
except (ValueError, AttributeError):
    pass

VIDEO_EXTENSIONS = {".mkv", ".mp4", ".avi"}

# Tier 1: search by series/season/episode text query, no video hash needed.
PROVIDERS_BY_NAME = ["gestdown", "podnapisi", "tvsubtitles"]

# Tier 2 fallback: opensubtitles also supports a metadata-only query
# (series + season + episode), used here purely as a fallback source -
# not for its hash-based lookup. bsplayer and getsubtitle only support
# hash-based lookup, so their hash is computed and injected for each video
# before querying (see HASH_PROVIDERS below).
PROVIDERS_FALLBACK = ["opensubtitles", "bsplayer", "getsubtitle"]

ALL_PROVIDERS = PROVIDERS_BY_NAME + PROVIDERS_FALLBACK

# Short description of each provider's matching approach, used to build a
# --force-<name> flag per provider (see main()).
PROVIDER_DESCRIPTIONS = {
    "gestdown": "name/metadata-based matching via TheTVDB",
    "podnapisi": "name/metadata-based matching",
    "tvsubtitles": "name/metadata-based matching",
    "opensubtitles": "metadata-based matching (opensubtitles.org)",
    "bsplayer": "hash-based lookup, which matches your exact release instead of guessing by series/season/episode",
    "getsubtitle": "hash-based lookup, which matches your exact release instead of guessing by series/season/episode",
}

# Providers that only support hash-based lookup (no name/metadata search),
# mapped to the class whose hash_video() computes the hash subliminal
# expects to find at video.hashes[name].
HASH_PROVIDERS = {"bsplayer": BSPlayerProvider, "getsubtitle": GetSubtitleProvider}

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


def find_videos(targets: list[Path]) -> list[Path]:
    """Resolve `targets` - each either a directory to scan recursively for
    video files, or a specific video file - into a sorted, deduplicated
    list of video files."""
    videos: set[Path] = set()
    for target in targets:
        if target.is_dir():
            videos.update(
                p for p in target.rglob("*")
                if p.is_file() and p.suffix.lower() in VIDEO_EXTENSIONS
            )
        elif target.is_file():
            videos.add(target)
        else:
            logger.warning("Path does not exist: %s", target)
    return sorted(videos)


def language_code(language: Language) -> str:
    return language.alpha2 or str(language)


def unique_subtitle_path(video_path: Path, language: Language, *, single: bool) -> Path:
    """Return a path to save a subtitle for `language` next to `video_path`
    that doesn't overwrite an existing file. Tries the normal clean name
    first (matching video_path exactly if `single`, otherwise with a
    language suffix); if that's taken, adds an incrementing numeric suffix
    instead of clobbering whatever's already there."""
    if single:
        candidate = video_path.with_suffix(".srt")
    else:
        candidate = video_path.parent / f"{video_path.stem}.{language_code(language)}.srt"
    if not candidate.exists():
        return candidate

    index = 2
    while True:
        candidate = video_path.parent / f"{video_path.stem}.{language_code(language)}.{index}.srt"
        if not candidate.exists():
            return candidate
        index += 1


def next_free_index(video_path: Path, lang_code: str) -> int:
    """Return the next unused index for "<video>.<lang_code>.<n>.srt" next
    to `video_path`, so repeated --all-versions runs add to what's already
    there instead of overwriting it."""
    index = 1
    while (video_path.parent / f"{video_path.stem}.{lang_code}.{index}.srt").exists():
        index += 1
    return index


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

    for name, provider_cls in HASH_PROVIDERS.items():
        if name not in providers:
            continue
        for path, video in scanned.items():
            video_hash = provider_cls.hash_video(str(path))
            if video_hash:
                video.hashes[name] = video_hash

    all_languages = set().union(*(needed[path] for path in scanned))
    subtitles = download_best_subtitles(set(scanned.values()), all_languages, providers=providers)

    for path, video in scanned.items():
        found = subtitles.get(video, [])
        for sub in found:
            if sub.content:
                out_path = unique_subtitle_path(path, sub.language, single=single)
                out_path.write_bytes(sub.content)
        remaining = needed[path] - {sub.language for sub in found}
        if not remaining:
            print(f"  OK       {path}")
        else:
            found_str = " (partial)" if found else ""
            print(f"  MISSING{found_str}  {path}")
            still_needed[path] = remaining

    return still_needed


def fetch_all_versions(needed: dict[Path, set[Language]], providers: list[str]) -> dict[Path, set[Language]]:
    """Like fetch_tier, but downloads and saves *every* candidate subtitle
    found for each video/language instead of picking a single best match.
    Saved as "<video>.<lang>.<index>.srt", since multiple files per
    video/language would otherwise overwrite each other."""
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

    for name, provider_cls in HASH_PROVIDERS.items():
        if name not in providers:
            continue
        for path, video in scanned.items():
            video_hash = provider_cls.hash_video(str(path))
            if video_hash:
                video.hashes[name] = video_hash

    all_languages = set().union(*(needed[path] for path in scanned))
    found = list_subtitles(set(scanned.values()), all_languages, providers=providers)

    for path, video in scanned.items():
        candidates = [sub for sub in found.get(video, []) if sub.language in needed[path]]
        if not candidates:
            print(f"  MISSING  {path}")
            still_needed[path] = needed[path]
            continue

        download_subtitles(candidates)
        downloaded = [sub for sub in candidates if sub.content]

        by_language: dict[Language, list[Subtitle]] = {}
        for sub in downloaded:
            by_language.setdefault(sub.language, []).append(sub)

        for language, subs in by_language.items():
            subs.sort(key=lambda sub: (sub.provider_name, str(sub.subtitle_id)))
            lang_code = language_code(language)
            start = next_free_index(path, lang_code)
            for offset, sub in enumerate(subs):
                out_path = path.parent / f"{path.stem}.{lang_code}.{start + offset}.srt"
                out_path.write_bytes(sub.content)

        remaining = needed[path] - set(by_language)
        if not remaining:
            print(f"  OK       {path}  ({len(downloaded)} version(s) across {len(by_language)} language(s))")
        else:
            found_str = " (partial)" if by_language else ""
            print(f"  MISSING{found_str}  {path}")
            still_needed[path] = remaining

    return still_needed


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    parser.add_argument("paths", nargs="*", default=["."],
                         help="Directories to scan recursively and/or specific video files to "
                              "target. Repeatable, can mix both. Default: current directory")
    parser.add_argument("-l", "--language", action="append", dest="languages",
                         default=None, help="Language code (IETF, e.g. en, sr). Repeatable. Default: en")
    parser.add_argument("--dry-run", action="store_true",
                         help="Only list the videos that would be searched, don't download")
    for name in ALL_PROVIDERS:
        parser.add_argument(f"--force-{name}", action="store_true",
                             help=f"Use only {name} ({PROVIDER_DESCRIPTIONS[name]}), skipping the "
                                  "other providers and the tier 1/tier 2 split. Combine multiple "
                                  "--force-* flags to use exactly that set.")
    parser.add_argument("--debug", action="store_true",
                         help="Show full tracebacks for provider failures instead of one-line "
                              "summaries, for troubleshooting a specific provider")
    parser.add_argument("--all-versions", action="store_true",
                         help="Download every matching subtitle found instead of just the best "
                              "one, saved as <video>.<lang>.<index>.srt. Searches all providers "
                              "in a single pass rather than tier by tier.")
    args = parser.parse_args()

    if not args.debug:
        logging.getLogger().handlers[0].addFilter(_QuietSubliminalTracebacks())

    configure_cache()

    targets = [Path(p).resolve() for p in args.paths]
    lang_codes = args.languages or ["en"]
    languages = {Language.fromietf(code) for code in lang_codes}
    single = len(languages) == 1

    print(f"Scanning {', '.join(str(t) for t in targets)} for video files...")
    videos = find_videos(targets)
    print(f"{len(videos)} video files found.")

    if not videos:
        print("Nothing to do.")
        return

    needed = {v: set(languages) for v in videos}

    if args.dry_run:
        lang_str = ", ".join(sorted(lang_codes))
        for v in videos:
            print(f"  SEARCH  {v}  [{lang_str}]")
        return

    selected_providers = [name for name in ALL_PROVIDERS if getattr(args, f"force_{name}")]

    if args.all_versions:
        providers = selected_providers or ALL_PROVIDERS
        print(f"\nAll versions ({', '.join(providers)}) for {len(needed)} video(s):")
        still_needed = fetch_all_versions(needed, providers)
    elif selected_providers:
        print(f"\nForced providers ({', '.join(selected_providers)}) for {len(needed)} video(s):")
        still_needed = fetch_tier(needed, selected_providers, single)
    else:
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
