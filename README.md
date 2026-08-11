# ECOS Resource Registry

This repository publishes the ECOS Studio Resource Manager registry.

The public registry URL is:

```text
https://<owner>.github.io/<repo>/tool-registry.json
```

Use it with ECOS Studio by setting:

```sh
ECOS_REGISTRY_URL=https://<owner>.github.io/<repo>/tool-registry.json
```

## Files

- `tool-registry.json`: deployed registry consumed by ECOS Studio.
- `examples/tool-registry.example.json`: non-empty template for tools, PDKs, and MPC source archives.
- `.github/workflows/ci.yml`: validates registry changes on pull requests and `main`.
- `.github/workflows/pages.yml`: validates the registry and deploys it to GitHub Pages.

## Registry Notes

- `schema_version` must be `2`.
- `tools`, `pdks`, and `mpcs` must be arrays.
- Put the newest version first in each `versions` array. ECOS Studio treats `versions[0]` as the latest version.
- Platform keys are produced by ECOS Studio, for example `linux-x86_64` and `darwin-arm64`.
- PDKs and MPC source archives may use `all-platform` for platform-independent archives.
- MPC `version` values must use sortable dotted numeric or date versions. Pin the archive URL to an immutable commit or release asset; do not use mutable branch URLs.
- Asset archives must be `.tar`, `.tar.gz`, `.tgz`, or `.zip`.
- `sha256` must match the archive bytes exactly.
- `size` is the archive size in bytes.
- `strip_prefix` is optional and removes a top-level archive directory during extraction.
- A platform may set `update_source` to a `github_branch` object with `branch`.
  The scheduled lock refresh resolves that branch on the entry's canonical GitHub
  `homepage` and replaces the archive URL and lock fields with an immutable commit archive.

## Local Validation

Run the same structure and URL reachability check used by CI:

```sh
python3 .github/scripts/validate_registry.py tool-registry.json --check-urls
```

For offline editing, skip live URL checks:

```sh
python3 .github/scripts/validate_registry.py tool-registry.json
```

## GitHub Pages Setup

After initializing this folder as a GitHub repository:

1. Push it to GitHub.
2. In repository settings, enable Pages with source `GitHub Actions`.
3. Push to `main` or run the `Deploy registry to GitHub Pages` workflow manually.
