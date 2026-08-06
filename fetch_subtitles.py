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

__version__ = "0.6.0"

import argparse
import base64
import logging
import zlib
from pathlib import Path

from babelfish import Language, language_converters
from defusedxml import ElementTree
from dogpile.cache.exception import RegionAlreadyConfigured
from requests import Session
from subliminal import scan_video, download_best_subtitles, download_subtitles, list_subtitles, save_subtitles
from subliminal.cache import region as subliminal_cache_region
from subliminal.core import search_external_subtitles
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


def find_videos(root: Path) -> list[Path]:
    return sorted(
        p for p in root.rglob("*")
        if p.is_file() and p.suffix.lower() in VIDEO_EXTENSIONS
    )


def missing_languages(video_path: Path, languages: set[Language], single: bool) -> set[Language]:
    """Return the subset of `languages` not already covered by a subtitle
    next to `video_path`."""
    existing = search_external_subtitles(video_path.name, directory=str(video_path.parent))
    have = {sub.language for sub in existing.values()}
    if single and Language("und") in have:
        # save_subtitles(single=True) saves without a language suffix (see
        # main()), so subliminal can't tell what language a file like
        # "Show S01E01.srt" is from its name alone and reports it as "und".
        # Since we're only tracking one language here, treat it as covering
        # that language - otherwise it looks missing on every run and gets
        # re-downloaded every time.
        return set()
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
            lang_code = language.alpha2 or str(language)
            for index, sub in enumerate(subs, start=1):
                out_path = path.parent / f"{path.stem}.{lang_code}.{index}.srt"
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
    parser.add_argument("root", nargs="?", default=".", help="Directory to scan (default: current directory)")
    parser.add_argument("-l", "--language", action="append", dest="languages",
                         default=None, help="Language code (IETF, e.g. en, sr). Repeatable. Default: en")
    parser.add_argument("--dry-run", action="store_true", help="Only report what's missing, don't download")
    parser.add_argument("--force-bsplayer", action="store_true",
                         help="Skip the name/metadata-based providers and use only bsplayer's "
                              "hash-based lookup, which matches your exact release instead of "
                              "guessing by series/season/episode")
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

    root = Path(args.root).resolve()
    lang_codes = args.languages or ["en"]
    languages = {Language.fromietf(code) for code in lang_codes}
    single = len(languages) == 1

    print(f"Scanning {root} for video files...")
    videos = find_videos(root)
    print(f"{len(videos)} video files found.")

    needed = {}
    for v in videos:
        langs = missing_languages(v, languages, single)
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

    if args.all_versions:
        providers = ["bsplayer"] if args.force_bsplayer else PROVIDERS_BY_NAME + PROVIDERS_FALLBACK
        print(f"\nAll versions ({', '.join(providers)}) for {len(needed)} video(s):")
        still_needed = fetch_all_versions(needed, providers)
    elif args.force_bsplayer:
        print(f"\nForced bsplayer (hash-based) lookup for {len(needed)} video(s):")
        still_needed = fetch_tier(needed, ["bsplayer"], single)
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
