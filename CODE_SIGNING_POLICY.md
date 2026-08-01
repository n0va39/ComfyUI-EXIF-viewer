# Code signing policy

## Service status

Free code signing provided by [SignPath.io](https://about.signpath.io/), certificate by [SignPath Foundation](https://signpath.org/).

The project is preparing its SignPath Foundation application. Until the application is accepted, only a release whose Authenticode signature validates successfully and whose release notes explicitly identify it as signed may be treated as a signed release.

## Scope

The policy covers only `ComfyUI-EXIF-viewer.exe` built from this repository. The project does not use its signing access for unrelated projects or for upstream third-party binaries.

The executable is portable: it does not install services, startup entries or system configuration. Users can remove it by clearing the app cache if desired and deleting the executable; the optional local-data directory is documented in `PRIVACY.md`.

## Team roles

- Authors and committers: [n0va39](https://github.com/n0va39)
- Reviewer for changes proposed by non-committers: [n0va39](https://github.com/n0va39)
- Signing-request approver: [n0va39](https://github.com/n0va39)

All team members with repository or SignPath access must use multi-factor authentication.

## Source and build requirements

- Release code must be committed to the protected `main` branch and pass its required CI and CodeQL checks.
- A release tag must point to the current `main` commit and match the application version.
- The Windows executable must be built on a GitHub-hosted runner using the repository build script and hash-locked dependencies.
- The unsigned executable must be uploaded as a GitHub Actions artifact before the signing request is submitted.
- SignPath origin verification must link the signing request to the repository, workflow run, commit and artifact.
- The SignPath artifact configuration must restrict the expected filename and PE product, version, company and copyright metadata.

## Approval and publication

Every release signing request requires manual approval. The approver checks the source commit, required checks, dependency audit, CodeQL result, version metadata and unsigned artifact origin before approval.

After signing, the workflow verifies the Authenticode chain and publisher, runs the signed executable's version check, regenerates `SHA256SUMS.txt`, and produces a signed release-candidate artifact. Publishing a GitHub Release remains a separate manual action.

Release notes for signed versions must include the SignPath attribution above, a link to this policy, the executable SHA-256, and the expected publisher. Unsigned builds must be identified as unsigned.

## Privacy and security reports

The application's network and local-data behavior is documented in [PRIVACY.md](PRIVACY.md). Vulnerabilities or suspected signing-policy violations should be reported using the private process in [SECURITY.md](SECURITY.md).
