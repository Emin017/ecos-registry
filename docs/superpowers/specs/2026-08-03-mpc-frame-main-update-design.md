# MPC Frame Main Update Design

## Scope

Refresh the published `mpc-frame` source archive in `tool-registry.json` to the
current `openecos-projects/mpc-frame` `main` commit
`7555b4053816895919fb1d324d623d46d70dec3d`. Keep the existing registry version
`0.1.0` and do not add a mutable branch URL.

## Registry Change

Only the `mpcs` entry with ID `mpc-frame` changes. Its `all-platform` payload
will point at GitHub's immutable archive URL for the selected commit. The
archive's exact byte size and SHA-256 digest will be calculated locally and
recorded beside the URL. `strip_prefix` will match GitHub's archive directory
name for that commit.

## Validation

Validate the modified registry against the local schema, then run the existing
registry unit tests. Also run the live URL validation so the pinned archive is
reachable and its recorded size and digest describe the downloaded bytes.

## Non-Goals

This change does not alter the MPC version number, the example registry, the
registry schema, or lock-refresh automation.
