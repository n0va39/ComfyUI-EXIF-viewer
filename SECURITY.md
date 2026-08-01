# Security Policy

## Supported versions

Security fixes are provided for the latest published release. Older portable EXE releases should be replaced with the latest release rather than kept in parallel.

## Reporting a vulnerability

Please use **Security > Report a vulnerability** in this GitHub repository. Do not open a public issue for a vulnerability that has not been fixed yet.

Include the affected version, input file or minimal reproduction, expected impact, and any crash or antivirus details that help reproduce the report. Do not include personal images or credentials unless they are strictly necessary and safe to share.

## Release integrity

Official Windows binaries are published only through this repository's GitHub Releases. Each release must include a SHA-256 checksum. A release marked as signed must have a valid Authenticode signature and follow the repository [Code signing policy](CODE_SIGNING_POLICY.md).

The project is preparing a SignPath Foundation application. Existing releases remain unsigned unless their Authenticode signature proves otherwise.
