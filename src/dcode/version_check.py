"""GitHub release version-check helper.

Stdlib-only. Used by `dcode update --check` (and later by `dcode doctor`)
to compare the local installed version against the latest GitHub release.
"""

from __future__ import annotations

import json
import re
import urllib.error
import urllib.request

_LATEST_URL = "https://api.github.com/repos/rosstaco/dcode/releases/latest"
_TAGS_URL = "https://api.github.com/repos/rosstaco/dcode/tags?per_page=1"
_RELEASE_TAG_URL = "https://github.com/rosstaco/dcode/releases/tag/{tag}"

_HEADERS = {
    "User-Agent": "dcode-doctor",
    "Accept": "application/vnd.github+json",
}

_NUM_PREFIX = re.compile(r"\d+(?:\.\d+)*")


class NetworkError(Exception):
    """Raised when the GitHub API is unreachable or returns an unexpected payload."""


def _fetch_json(url: str, timeout: float):
    req = urllib.request.Request(url, headers=_HEADERS)
    with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310
        data = resp.read()
    return json.loads(data)


def get_latest_release(timeout: float = 3.0) -> dict:
    """Return ``{"tag_name": str, "html_url": str}`` for the latest release.

    Hits ``/releases/latest`` first; on HTTP 404 falls back to
    ``/tags?per_page=1`` and synthesizes ``html_url``. Any network/parse
    failure is wrapped as :class:`NetworkError`.
    """
    try:
        try:
            payload = _fetch_json(_LATEST_URL, timeout)
        except urllib.error.HTTPError as exc:
            if exc.code != 404:
                raise
            tags = _fetch_json(_TAGS_URL, timeout)
            if not isinstance(tags, list) or not tags:
                raise NetworkError("no tags found in fallback /tags response") from None
            tag = tags[0].get("name")
            if not tag:
                raise NetworkError("fallback /tags entry missing 'name'") from None
            return {
                "tag_name": tag,
                "html_url": _RELEASE_TAG_URL.format(tag=tag),
            }
        tag = payload.get("tag_name")
        html_url = payload.get("html_url")
        if not tag or not html_url:
            raise NetworkError("release payload missing 'tag_name' or 'html_url'")
        return {"tag_name": tag, "html_url": html_url}
    except NetworkError:
        raise
    except (
        urllib.error.URLError,
        urllib.error.HTTPError,
        TimeoutError,
        OSError,
        ValueError,
        json.JSONDecodeError,
    ) as exc:
        raise NetworkError(f"failed to fetch latest release: {exc}") from exc


def parse_version(s: str) -> tuple[tuple[int, ...], bool]:
    """Parse a version string into ``((nums...), is_dev)``.

    Strips a leading ``v``, extracts the leading ``\\d+(\\.\\d+)*`` prefix.
    ``is_dev`` is ``True`` when the input has any non-numeric, non-dot
    suffix beyond the numeric prefix (excluding a bare ``+local`` segment).

    Raises :class:`ValueError` when no leading numeric prefix is present.
    """
    if not isinstance(s, str) or not s.strip():
        raise ValueError(f"invalid version string: {s!r}")
    stripped = s.strip().lstrip("v")
    m = _NUM_PREFIX.match(stripped)
    if not m:
        raise ValueError(f"no numeric prefix in version: {s!r}")
    nums = tuple(int(p) for p in m.group(0).split("."))
    rest = stripped[m.end():]
    # ".dev3", ".post1", ".rc1" etc. → dev/pre/post (treated as ahead).
    # "+g1234" alone (no .dev) is a local segment, treat as equal.
    is_dev = bool(rest) and not rest.startswith("+")
    return (nums, is_dev)


def compare_versions(local: str, latest: str) -> int:
    """Return ``-1`` if ``local < latest``, ``0`` if equal, ``1`` if ``local > latest``.

    Comparison is purely on the numeric prefix; dev/post suffixes are
    ignored here so that a dev build of ``0.4.2`` is treated as equal to
    the released ``0.4.2`` (per plan §11 "load-bearing" rule). Callers
    that need to surface "ahead via dev" can re-parse with
    :func:`parse_version` and inspect the ``is_dev`` flag.
    """
    pa, _ = parse_version(local)
    pb, _ = parse_version(latest)
    if pa < pb:
        return -1
    if pa > pb:
        return 1
    return 0
