from __future__ import annotations

import copy
import importlib.util
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
VALIDATOR_PATH = ROOT / ".github" / "scripts" / "validate_registry.py"

spec = importlib.util.spec_from_file_location("validate_registry", VALIDATOR_PATH)
assert spec is not None
validate_registry = importlib.util.module_from_spec(spec)
assert spec.loader is not None
spec.loader.exec_module(validate_registry)


def valid_registry() -> dict[str, object]:
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


class ValidateRegistryOfflineTests(unittest.TestCase):
    def errors_for(self, registry: object) -> list[str]:
        return validate_registry.validate_registry_data(registry)

    def assert_has_error(self, errors: list[str], expected: str) -> None:
        self.assertTrue(
            any(expected in error for error in errors),
            f"Expected {expected!r} in errors:\n" + "\n".join(errors),
        )

    def test_current_registry_passes_offline_validation(self) -> None:
        errors = validate_registry.validate_registry(ROOT / "tool-registry.json")

        self.assertEqual([], errors)

    def test_invalid_json_shape_reports_pathful_errors(self) -> None:
        self.assert_has_error(self.errors_for([]), "$: must be a JSON object")

        registry = {"schema_version": 1, "tools": {}, "pdks": {}, "extra": True}
        errors = self.errors_for(registry)

        self.assert_has_error(errors, "schema_version: must equal 2")
        self.assert_has_error(errors, "tools: must be an array")
        self.assert_has_error(errors, "pdks: must be an array")
        self.assert_has_error(errors, "extra: unknown top-level key")

    def test_required_fields_and_identifier_rules_are_enforced(self) -> None:
        registry = valid_registry()
        tool = registry["tools"][0]
        assert isinstance(tool, dict)
        tool["name"] = "Bad Name"
        del tool["display_name"]
        pdk = registry["pdks"][0]
        assert isinstance(pdk, dict)
        pdk["id"] = ""
        del pdk["description"]

        errors = self.errors_for(registry)

        self.assert_has_error(errors, "tools[0].display_name: missing required field")
        self.assert_has_error(errors, "tools[0].name: must match")
        self.assert_has_error(errors, "pdks[0].description: missing required field")
        self.assert_has_error(errors, "pdks[0].id: must be a non-empty stable identifier")

    def test_duplicate_entry_ids_and_empty_versions_or_platforms_fail(self) -> None:
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

        errors = self.errors_for(registry)

        self.assert_has_error(errors, "tools[1].name: duplicate tool name 'yosys'")
        self.assert_has_error(errors, "pdks[1].id: duplicate PDK id 'ics55'")
        self.assert_has_error(errors, "tools[0].versions: must be a non-empty array")
        self.assert_has_error(
            errors,
            "pdks[0].versions[0].platforms: must be a non-empty object",
        )

    def test_version_entries_and_ordering_are_validated(self) -> None:
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

        errors = self.errors_for(registry)

        self.assert_has_error(errors, "tools[0].versions: newest version must appear first")
        self.assert_has_error(errors, "tools[0].versions[0].requires: must be an array")
        self.assert_has_error(
            errors,
            "pdks[0].versions: mixed or unsupported version format",
        )

    def test_platform_keys_and_fields_are_validated(self) -> None:
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

        errors = self.errors_for(registry)

        self.assert_has_error(
            errors,
            "tools[0].versions[0].platforms.all-platform: all-platform is not allowed for tools",
        )
        self.assert_has_error(errors, "tools[0].versions[0].platforms: platform key must be non-empty")
        self.assert_has_error(errors, "tools[0].versions[0].platforms.linux-x86_64.url: must use http or https")
        self.assert_has_error(errors, "tools[0].versions[0].platforms.linux-x86_64.url: unsupported archive suffix")
        self.assert_has_error(errors, "tools[0].versions[0].platforms.linux-x86_64.sha256: must be a lowercase 64-character hex string")
        self.assert_has_error(errors, "tools[0].versions[0].platforms.linux-x86_64.size: must be a positive integer")
        self.assert_has_error(errors, "tools[0].versions[0].platforms.linux-x86_64.strip_prefix: must be a non-empty string")
        self.assert_has_error(errors, "tools[0].versions[0].platforms.linux-x86_64.unknown: unknown platform field")
        self.assert_has_error(errors, "pdks[0].versions[0].platforms.all-platform.url: unsupported archive suffix")

    def test_post_install_commands_are_validated(self) -> None:
        registry = valid_registry()
        platform = registry["pdks"][0]["versions"][0]["platforms"]["all-platform"]
        assert isinstance(platform, dict)
        platform["post_install"] = [
            {},
            {"command": []},
            {"command": ["make", 12], "cwd": "/tmp/build"},
            {"command": ["make"], "cwd": ".."},
        ]

        errors = self.errors_for(registry)

        self.assert_has_error(
            errors,
            "pdks[0].versions[0].platforms.all-platform.post_install[0].command: missing required field",
        )
        self.assert_has_error(
            errors,
            "pdks[0].versions[0].platforms.all-platform.post_install[1].command: must be a non-empty string array",
        )
        self.assert_has_error(
            errors,
            "pdks[0].versions[0].platforms.all-platform.post_install[2].command[1]: must be a string",
        )
        self.assert_has_error(
            errors,
            "pdks[0].versions[0].platforms.all-platform.post_install[2].cwd: must be a non-empty relative path",
        )
        self.assert_has_error(
            errors,
            "pdks[0].versions[0].platforms.all-platform.post_install[3].cwd: must stay inside the extracted resource",
        )


if __name__ == "__main__":
    unittest.main()
