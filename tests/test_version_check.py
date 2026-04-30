"""Tests for ``dcode.version_check``."""

from __future__ import annotations

import json
import urllib.error
from unittest.mock import MagicMock, patch

import pytest

from dcode import version_check
from dcode.version_check import (
    NetworkError,
    compare_versions,
    get_latest_release,
    parse_version,
)

# ---------- helpers ----------


class _FakeResp:
    def __init__(self, payload: bytes):
        self._payload = payload

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def read(self):
        return self._payload


def _http_error(code: int) -> urllib.error.HTTPError:
    return urllib.error.HTTPError(
        url="https://api.github.com/x", code=code, msg="err", hdrs=None, fp=None
    )


# ---------- get_latest_release ----------


def test_get_latest_release_happy_path():
    payload = json.dumps(
        {
            "tag_name": "v0.4.2",
            "name": "v0.4.2",
            "html_url": "https://github.com/rosstaco/dcode/releases/tag/v0.4.2",
        }
    ).encode()
    with patch.object(version_check.urllib.request, "urlopen", return_value=_FakeResp(payload)):
        info = get_latest_release()
    assert info["tag_name"] == "v0.4.2"
    assert info["html_url"] == "https://github.com/rosstaco/dcode/releases/tag/v0.4.2"


def test_get_latest_release_404_falls_back_to_tags():
    tags_payload = json.dumps([{"name": "v0.4.2"}]).encode()

    def _side_effect(req, timeout):  # noqa: ARG001
        url = req.full_url
        if url.endswith("/releases/latest"):
            raise _http_error(404)
        return _FakeResp(tags_payload)

    with patch.object(version_check.urllib.request, "urlopen", side_effect=_side_effect):
        info = get_latest_release()
    assert info["tag_name"] == "v0.4.2"
    assert info["html_url"] == "https://github.com/rosstaco/dcode/releases/tag/v0.4.2"


def test_get_latest_release_empty_tags_raises():
    def _side_effect(req, timeout):  # noqa: ARG001
        if req.full_url.endswith("/releases/latest"):
            raise _http_error(404)
        return _FakeResp(b"[]")

    with (
        patch.object(version_check.urllib.request, "urlopen", side_effect=_side_effect),
        pytest.raises(NetworkError),
    ):
        get_latest_release()


def test_get_latest_release_urlerror_raises_network_error():
    with (
        patch.object(
            version_check.urllib.request,
            "urlopen",
            side_effect=urllib.error.URLError("boom"),
        ),
        pytest.raises(NetworkError),
    ):
        get_latest_release()


def test_get_latest_release_timeout_raises_network_error():
    with (
        patch.object(
            version_check.urllib.request,
            "urlopen",
            side_effect=TimeoutError("slow"),
        ),
        pytest.raises(NetworkError),
    ):
        get_latest_release()


def test_get_latest_release_malformed_json_raises():
    with (
        patch.object(
            version_check.urllib.request,
            "urlopen",
            return_value=_FakeResp(b"not-json"),
        ),
        pytest.raises(NetworkError),
    ):
        get_latest_release()


def test_get_latest_release_missing_fields_raises():
    payload = json.dumps({"tag_name": "v0.4.2"}).encode()  # no html_url
    with (
        patch.object(
            version_check.urllib.request,
            "urlopen",
            return_value=_FakeResp(payload),
        ),
        pytest.raises(NetworkError),
    ):
        get_latest_release()


def test_get_latest_release_non_404_http_error_raises():
    with (
        patch.object(
            version_check.urllib.request,
            "urlopen",
            side_effect=_http_error(500),
        ),
        pytest.raises(NetworkError),
    ):
        get_latest_release()


def test_get_latest_release_sends_required_headers():
    captured: dict = {}

    def _side_effect(req, timeout):  # noqa: ARG001
        captured["headers"] = dict(req.header_items())
        return _FakeResp(json.dumps({"tag_name": "v0.4.2", "html_url": "x"}).encode())

    with patch.object(version_check.urllib.request, "urlopen", side_effect=_side_effect):
        get_latest_release()

    # urllib normalises header names to title case.
    assert captured["headers"].get("User-agent") == "dcode-doctor"
    assert captured["headers"].get("Accept") == "application/vnd.github+json"


# ---------- parse_version ----------


@pytest.mark.parametrize(
    ("inp", "expected"),
    [
        ("0", ((0,), False)),
        ("0.4", ((0, 4), False)),
        ("0.4.2", ((0, 4, 2), False)),
        ("v0.4.2", ((0, 4, 2), False)),
        ("0.4.2.dev0+g1234", ((0, 4, 2), True)),
        ("0.4.2.dev3+g1234", ((0, 4, 2), True)),
        ("0.4.2+local", ((0, 4, 2), False)),  # bare local segment is not "dev"
    ],
)
def test_parse_version_table(inp, expected):
    assert parse_version(inp) == expected


@pytest.mark.parametrize("bad", ["abc", "", "   ", "v"])
def test_parse_version_invalid_raises(bad):
    with pytest.raises(ValueError):
        parse_version(bad)


# ---------- compare_versions ----------


@pytest.mark.parametrize(
    ("a", "b", "expected"),
    [
        ("0.4.2", "0.4.2", 0),
        ("0.4.1", "0.4.2", -1),
        ("0.4.3", "0.4.2", 1),
        ("0.4.2.dev0+g1234", "0.4.2", 0),  # dev of same numeric prefix == not behind
        ("0.5.0.dev0", "0.4.2", 1),
        ("v0.4.2", "0.4.2", 0),
        ("0.4.2", "0.4.2.dev0+g1234", 0),
        ("0.4.2.dev0+g1234", "0.4.3", -1),
    ],
)
def test_compare_versions_table(a, b, expected):
    assert compare_versions(a, b) == expected


def test_compare_versions_dev_against_release_is_not_behind():
    # The load-bearing case from plan §11: this checkout's local version
    # parses as ahead-or-equal-to the released 0.4.2.
    assert compare_versions("0.4.2.dev0+g5f9826397.d20260427", "0.4.2") == 0


def test_module_uses_real_urllib_request_module_attribute():
    # Sanity: the patches above target version_check.urllib.request.urlopen,
    # which only works if the module imports urllib.request.
    assert hasattr(version_check.urllib.request, "urlopen")


# Avoid unused-import lint warning on MagicMock — keep handy for future tests.
_ = MagicMock
