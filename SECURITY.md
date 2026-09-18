# Security policy

## Reporting a vulnerability

Please report privately through GitHub's **Report a vulnerability** button on the
[Security tab](https://github.com/DayOnly/CBBEtoUBE-exe/security), not in a public
issue. That opens a private advisory only the maintainer can see.

If the button is not available to you, open an issue asking for a private channel
and say nothing about the details in it.

## What this program is

A desktop tool that converts mesh files on your own machine. It has no server, no
account, and no telemetry: it makes no network connections at all, and the release
build ships without the modules that could (see README, "the tool never goes
online"). The realistic risk surface is therefore the release artefact and the
files it reads:

- **The release zip.** Every build carries `SHA256SUMS` and a `VERSION.txt`, and
  `scripts/release_gate.py` verifies a published zip file for file against the
  tagged source. If a download does not match, say so in a report.
- **Untrusted mod files.** The converter parses third-party meshes, archives and
  plugins. A crash or a hang on a malformed file is a bug worth reporting; a
  crafted file that makes it write outside its output folder is a vulnerability.

## Supported versions

The latest release. Fixes land on `main` and ship in the next release.
