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
- `examples/tool-registry.example.json`: non-empty template for tools and PDKs.
- `.github/workflows/pages.yml`: validates the registry and deploys it to GitHub Pages.

## Registry Notes

- `schema_version` must be `2`.
- `tools` and `pdks` must be arrays.
- Put the newest version first in each `versions` array. ECOS Studio treats `versions[0]` as the latest version.
- Platform keys are produced by ECOS Studio, for example `linux-x86_64` and `darwin-arm64`.
- PDKs may use `all-platform` for platform-independent archives.
- Asset archives must be `.tar`, `.tar.gz`, `.tgz`, or `.zip`.
- `sha256` must match the archive bytes exactly.
- `size` is the archive size in bytes.
- `strip_prefix` is optional and removes a top-level archive directory during extraction.

## Frontend Tool Notes

ECOS Studio frontend tools should be managed here, not in the application
source tree. Runtime code reads this registry, installs the selected archive,
then injects the installed tool paths into ECC-FE through `PATH` and explicit
environment variables.

- `yosys` currently uses the OSS CAD Suite archive. That package also contains
  `verilator`, so ECOS Studio can expose Verilator through the same installed
  tool root without downloading a separate Verilator archive.
- `slang` uses the official upstream Linux release archive.
- `riscv-toolchain` uses the xPack RISC-V bare-metal GCC archive.
- `surfer` must point to ECOS Studio compatible web assets containing
  `index.html`, `integration.js`, `surfer.js`, and `surfer_bg.wasm`. The
  upstream Surfer desktop zip is a native application package and is not a
  valid replacement for those web assets.

## GitHub Pages Setup

After initializing this folder as a GitHub repository:

1. Push it to GitHub.
2. In repository settings, enable Pages with source `GitHub Actions`.
3. Push to `main` or run the `Deploy registry to GitHub Pages` workflow manually.
