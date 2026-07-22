from __future__ import annotations

import copy
import importlib.util
import sys
import tempfile
import unittest
from http.client import HTTPMessage
from pathlib import Path
from typing import TYPE_CHECKING, Any
from urllib.error import HTTPError

if TYPE_CHECKING:
    from urllib.request import Request

ROOT = Path(__file__).resolve().parents[1]
VALIDATOR_PATH = ROOT / ".github" / "scripts" / "validate_registry.py"

spec = importlib.util.spec_from_file_location("validate_registry", VALIDATOR_PATH)
assert spec is not None
validate_registry: Any = importlib.util.module_from_spec(spec)
assert spec.loader is not None
sys.modules[spec.name] = validate_registry
spec.loader.exec_module(validate_registry)

Issue = validate_registry.Issue
Rule = validate_registry.Rule

# An expectation is a list of (path, rule) pairs, in emission order. Each test
# reads as a spec clause: at this JSON path, this rule — and only these rules —
# must fire. Message wording is deliberately not asserted.
Expectation = tuple[str, Rule]


def valid_registry() -> dict[str, Any]:
    return {
        "schema_version": 2,
        "tools": [
            {
                "name": "yosys",
                "display_name": "Yosys",
                "description": "Yosys from OSS CAD Suite.",
                "category": "synthesis",
                "homepage": "https://github.com/YosysHQ/oss-cad-suite-build",
                "versions": [
                    {
                        "version": "2026-05-13",
                        "platforms": {
                            "linux-x86_64": {
                                "url": "https://example.com/yosys.tar.gz",
                                "sha256": "a" * 64,
                                "size": 123,
                                "strip_prefix": "oss-cad-suite",
                            }
                        },
                        "requires": [],
                    }
                ],
            }
        ],
        "pdks": [
            {
                "id": "ics55",
                "display_name": "ICsprout 55nm PDK",
                "description": "ICsprout 55nm open-source process design kit.",
                "category": "pdk",
                "homepage": "https://github.com/openecos-projects/icsprout55-pdk",
                "versions": [
                    {
                        "version": "1.10.100",
                        "platforms": {
                            "all-platform": {
                                "url": "https://example.com/ics55.zip",
                                "sha256": "b" * 64,
                                "size": 456,
                                "strip_prefix": "ics55",
                                "post_install": [
                                    {
                                        "command": ["make", "unzip"],
                                        "cwd": ".",
                                    }
                                ],
                            }
                        },
                    }
                ],
            }
        ],
    }


def issues_in(registry: object, **kwargs: Any) -> list[Issue]:
    return validate_registry.validate_registry_data(registry, **kwargs)


def paths_and_rules(registry: object, **kwargs: Any) -> list[Expectation]:
    return [(issue.path, issue.rule) for issue in issues_in(registry, **kwargs)]


class ValidateRegistryOfflineTests(unittest.TestCase):
    def assert_spec(
        self,
        registry: object,
        expected: list[Expectation],
        **kwargs: Any,
    ) -> None:
        self.assertEqual(expected, paths_and_rules(registry, **kwargs))

    def test_current_registry_passes_offline_validation(self) -> None:
        """Validate that the checked-in registry satisfies offline format rules."""
        issues = validate_registry.validate_registry(ROOT / "tool-registry.json")
        self.assertEqual([], issues)

    def test_issue_str_renders_pathful_message(self) -> None:
        """Keep the CLI line format stable: '<path>: <message>'."""
        issue = Issue(
            "tools[0].name", Rule.IDENTIFIER_FORMAT, "must match ^[a-z0-9_-]+$"
        )

        self.assertEqual("tools[0].name: must match ^[a-z0-9_-]+$", str(issue))

    def test_unparseable_registry_file_reports_invalid_json(self) -> None:
        """Report a JSON syntax error as a single root-level invalid-json issue."""
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "broken.json"
            path.write_text("{not json", encoding="utf-8")
            issues = validate_registry.validate_registry(path)

        self.assertEqual([("$", Rule.INVALID_JSON)], [(i.path, i.rule) for i in issues])

    def test_invalid_json_shape_reports_pathful_errors(self) -> None:
        """Verify top-level JSON shape errors point at the offending keys."""
        self.assert_spec([], [("$", Rule.EXPECTED_OBJECT)])

        registry = {"schema_version": 1, "tools": {}, "pdks": {}, "extra": True}

        self.assert_spec(
            registry,
            [
                ("schema_version", Rule.SCHEMA_VERSION),
                ("extra", Rule.UNKNOWN_TOP_LEVEL_KEY),
                ("tools", Rule.EXPECTED_ARRAY),
                ("pdks", Rule.EXPECTED_ARRAY),
            ],
        )

    def test_required_fields_and_identifier_rules_are_enforced(self) -> None:
        """Check required entry fields and stable identifier naming constraints."""
        registry = valid_registry()
        tool = registry["tools"][0]
        assert isinstance(tool, dict)
        tool["name"] = "Bad Name"
        del tool["display_name"]
        pdk = registry["pdks"][0]
        assert isinstance(pdk, dict)
        pdk["id"] = ""
        del pdk["description"]

        self.assert_spec(
            registry,
            [
                ("tools[0].display_name", Rule.REQUIRED),
                ("tools[0].name", Rule.IDENTIFIER_FORMAT),
                ("pdks[0].description", Rule.REQUIRED),
                ("pdks[0].id", Rule.IDENTIFIER_EMPTY),
            ],
        )

    def test_duplicate_entry_ids_and_empty_versions_or_platforms_fail(self) -> None:
        """Reject duplicate tool/PDK ids and empty version or platform sections."""
        registry = valid_registry()
        tool = copy.deepcopy(registry["tools"][0])
        pdk = copy.deepcopy(registry["pdks"][0])
        assert isinstance(registry["tools"], list)
        assert isinstance(registry["pdks"], list)
        registry["tools"].append(tool)
        registry["pdks"].append(pdk)
        first_tool = registry["tools"][0]
        first_pdk = registry["pdks"][0]
        assert isinstance(first_tool, dict)
        assert isinstance(first_pdk, dict)
        first_tool["versions"] = []
        versions = first_pdk["versions"]
        assert isinstance(versions, list)
        platforms = versions[0]["platforms"]
        assert isinstance(platforms, dict)
        platforms.clear()

        self.assert_spec(
            registry,
            [
                ("tools[0].versions", Rule.NON_EMPTY_ARRAY),
                ("tools[1].name", Rule.DUPLICATE_ID),
                ("pdks[0].versions[0].platforms", Rule.NON_EMPTY_OBJECT),
                ("pdks[1].id", Rule.DUPLICATE_ID),
            ],
        )

    def test_version_entries_and_ordering_are_validated(self) -> None:
        """Ensure requires type and newest-first version order are checked."""
        registry = valid_registry()
        tool = registry["tools"][0]
        pdk = registry["pdks"][0]
        assert isinstance(tool, dict)
        assert isinstance(pdk, dict)
        tool["versions"] = [
            {
                "version": "2026-01-01",
                "platforms": copy.deepcopy(tool["versions"][0]["platforms"]),
                "requires": "yosys",
            },
            {
                "version": "2026-05-13",
                "platforms": copy.deepcopy(tool["versions"][0]["platforms"]),
            },
        ]
        pdk["versions"] = [
            {
                "version": "1.9.9",
                "platforms": copy.deepcopy(pdk["versions"][0]["platforms"]),
            },
            {
                "version": "1.10.100",
                "platforms": copy.deepcopy(pdk["versions"][0]["platforms"]),
            },
            {
                "version": "custom",
                "platforms": copy.deepcopy(pdk["versions"][0]["platforms"]),
            },
        ]

        self.assert_spec(
            registry,
            [
                ("tools[0].versions[0].requires", Rule.EXPECTED_ARRAY),
                ("tools[0].versions", Rule.VERSION_ORDER),
                ("pdks[0].versions", Rule.MIXED_VERSION_FORMAT),
            ],
        )

    def test_platform_keys_and_fields_are_validated(self) -> None:
        """Verify platform names, asset fields, and archive metadata constraints."""
        registry = valid_registry()
        tool_version = registry["tools"][0]["versions"][0]
        pdk_version = registry["pdks"][0]["versions"][0]
        assert isinstance(tool_version, dict)
        assert isinstance(pdk_version, dict)
        tool_version["platforms"] = {
            "all-platform": copy.deepcopy(tool_version["platforms"]["linux-x86_64"]),
            "": copy.deepcopy(tool_version["platforms"]["linux-x86_64"]),
            "linux-x86_64": {
                "url": "ftp://example.com/yosys.bin",
                "sha256": "A" * 64,
                "size": 0,
                "strip_prefix": "",
                "unknown": True,
            },
        }
        pdk_version["platforms"]["all-platform"]["url"] = "https://example.com/pdk.dmg"

        self.assert_spec(
            registry,
            [
                (
                    "tools[0].versions[0].platforms.all-platform",
                    Rule.ALL_PLATFORM_FOR_TOOL,
                ),
                (
                    "tools[0].versions[0].platforms",
                    Rule.EMPTY_PLATFORM_KEY,
                ),
                (
                    "tools[0].versions[0].platforms.linux-x86_64.unknown",
                    Rule.UNKNOWN_PLATFORM_FIELD,
                ),
                (
                    "tools[0].versions[0].platforms.linux-x86_64.url",
                    Rule.URL_SCHEME,
                ),
                (
                    "tools[0].versions[0].platforms.linux-x86_64.url",
                    Rule.ARCHIVE_SUFFIX,
                ),
                (
                    "tools[0].versions[0].platforms.linux-x86_64.sha256",
                    Rule.SHA256_FORMAT,
                ),
                (
                    "tools[0].versions[0].platforms.linux-x86_64.size",
                    Rule.POSITIVE_INTEGER,
                ),
                (
                    "tools[0].versions[0].platforms.linux-x86_64.strip_prefix",
                    Rule.NON_EMPTY_STRING,
                ),
                (
                    "pdks[0].versions[0].platforms.all-platform.url",
                    Rule.ARCHIVE_SUFFIX,
                ),
            ],
        )

    def test_malformed_url_errors_are_pathful_offline(self) -> None:
        """Confirm malformed asset URLs fail offline with the exact platform path."""
        for url in (
            "https://[bad/foo.tar.gz",
            "https://exa mple.com/yosys.tar.gz",
            "http://:80/yosys.tar.gz",
        ):
            with self.subTest(url=url):
                registry = valid_registry()
                platform = registry["tools"][0]["versions"][0]["platforms"][
                    "linux-x86_64"
                ]
                assert isinstance(platform, dict)
                platform["url"] = url

                self.assert_spec(
                    registry,
                    [
                        (
                            "tools[0].versions[0].platforms.linux-x86_64.url",
                            Rule.MALFORMED_URL,
                        )
                    ],
                )

    def test_post_install_commands_are_validated(self) -> None:
        """Check post-install command arrays and cwd sandbox boundaries."""
        registry = valid_registry()
        platform = registry["pdks"][0]["versions"][0]["platforms"]["all-platform"]
        assert isinstance(platform, dict)
        platform["post_install"] = [
            {},
            {"command": []},
            {"command": ["make", 12], "cwd": "/tmp/build"},
            {"command": ["make"], "cwd": ".."},
            {"command": ["make"], "cwd": "C:\\tmp"},
            {"command": ["make"], "cwd": "\\tmp"},
            {"command": ["make"], "cwd": "C:tmp"},
        ]

        post_install = "pdks[0].versions[0].platforms.all-platform.post_install"
        self.assert_spec(
            registry,
            [
                (f"{post_install}[0].command", Rule.REQUIRED),
                (f"{post_install}[1].command", Rule.COMMAND_ARRAY),
                (f"{post_install}[2].command[1]", Rule.EXPECTED_STRING),
                (f"{post_install}[2].cwd", Rule.CWD_RELATIVE),
                (f"{post_install}[3].cwd", Rule.CWD_ESCAPE),
                (f"{post_install}[4].cwd", Rule.CWD_RELATIVE),
                (f"{post_install}[5].cwd", Rule.CWD_RELATIVE),
                (f"{post_install}[6].cwd", Rule.CWD_RELATIVE),
            ],
        )


class FakeResponse:
    def __init__(self, status: int) -> None:
        self.status = status
        self.read_sizes: list[int | None] = []

    def __enter__(self) -> FakeResponse:
        return self

    def __exit__(self, *args: object) -> None:
        return None

    def read(self, size: int | None = None) -> bytes:
        self.read_sizes.append(size)
        return b"x"


class ValidateRegistryUrlTests(unittest.TestCase):
    def test_url_checking_reports_registry_path_and_url(self) -> None:
        """Ensure URL check failures include both registry path and failing URL."""
        registry = valid_registry()

        def checker(url: str) -> str | None:
            if "yosys" in url:
                return f"mock failure for {url}"
            return None

        issues = issues_in(registry, check_urls=True, url_checker=checker)

        self.assertEqual(
            [("tools[0].versions[0].platforms.linux-x86_64.url", Rule.URL_UNREACHABLE)],
            [(issue.path, issue.rule) for issue in issues],
        )
        self.assertIn(
            "mock failure for https://example.com/yosys.tar.gz",
            issues[0].message,
        )

    def test_url_checker_accepts_successful_head(self) -> None:
        """Accept a successful HEAD response without issuing a fallback GET."""
        requests: list[Request] = []

        def opener(request: Request, timeout: float) -> FakeResponse:
            requests.append(request)
            self.assertEqual(5.0, timeout)
            return FakeResponse(200)

        error = validate_registry.check_url_reachable(
            "https://example.com/yosys.tar.gz",
            opener=opener,
            timeout=5.0,
        )

        self.assertIsNone(error)
        self.assertEqual(["HEAD"], [request.get_method() for request in requests])

    def test_default_url_checker_passes_timeout_as_keyword(self) -> None:
        """Verify the default urlopen adapter passes timeout as a keyword argument."""
        calls: list[float] = []
        original_urlopen = validate_registry.urlopen

        def fake_urlopen(request: Request, *, timeout: float) -> FakeResponse:
            self.assertEqual("HEAD", request.get_method())
            calls.append(timeout)
            return FakeResponse(200)

        validate_registry.urlopen = fake_urlopen
        try:
            error = validate_registry.check_url_reachable(
                "https://example.com/yosys.tar.gz",
                timeout=7.0,
            )
        finally:
            validate_registry.urlopen = original_urlopen

        self.assertIsNone(error)
        self.assertEqual([7.0], calls)

    def test_url_checker_falls_back_to_ranged_get_without_full_download(self) -> None:
        """Use a one-byte ranged GET fallback when HEAD is not supported."""
        requests: list[Request] = []
        get_response = FakeResponse(206)

        def opener(request: Request, timeout: float) -> FakeResponse:
            del timeout
            requests.append(request)
            if request.get_method() == "HEAD":
                raise HTTPError(
                    request.full_url,
                    405,
                    "Method Not Allowed",
                    HTTPMessage(),
                    None,
                )
            return get_response

        error = validate_registry.check_url_reachable(
            "https://example.com/yosys.tar.gz",
            opener=opener,
        )

        self.assertIsNone(error)
        methods = [request.get_method() for request in requests]
        self.assertEqual(["HEAD", "GET"], methods)
        self.assertEqual("bytes=0-0", requests[1].headers["Range"])
        self.assertEqual([1], get_response.read_sizes)

    def test_url_checker_reports_timeout_and_non_success_status(self) -> None:
        """Report timeout and HTTP status failures from lightweight URL probes."""
        def timeout_opener(request: Request, timeout: float) -> FakeResponse:
            del request, timeout
            raise TimeoutError("timed out")

        timeout_error = validate_registry.check_url_reachable(
            "https://example.com/yosys.tar.gz",
            opener=timeout_opener,
        )

        self.assertIsNotNone(timeout_error)
        self.assertIn("timed out", timeout_error)

        def not_found_opener(request: Request, timeout: float) -> FakeResponse:
            del timeout
            raise HTTPError(request.full_url, 404, "Not Found", HTTPMessage(), None)

        status_error = validate_registry.check_url_reachable(
            "https://example.com/yosys.tar.gz",
            opener=not_found_opener,
        )

        self.assertIsNotNone(status_error)
        self.assertIn("GET returned HTTP 404", status_error)

    def test_url_checking_reports_malformed_url_without_crashing(self) -> None:
        """Return a normal URL-check error for malformed URLs instead of crashing."""
        error = validate_registry.check_url_reachable(
            "https://exa mple.com/yosys.tar.gz"
        )

        self.assertIsNotNone(error)
        self.assertIn("failed", error)


if __name__ == "__main__":
    unittest.main()
