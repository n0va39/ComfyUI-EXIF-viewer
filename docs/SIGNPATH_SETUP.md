# SignPath Foundation setup

This document separates repository preparation from actions that require the maintainer's SignPath account or approval.

## Before applying

1. Confirm that multi-factor authentication is enabled for the `n0va39` GitHub account.
2. Review the [SignPath Foundation terms](https://signpath.org/terms.html) and the repository [code signing policy](../CODE_SIGNING_POLICY.md).
3. Apply at <https://signpath.org/apply> using:
   - Project: `ComfyUI-EXIF-viewer`
   - Repository: `https://github.com/n0va39/ComfyUI-EXIF-viewer`
   - License: MIT
   - Latest binary release: `https://github.com/n0va39/ComfyUI-EXIF-viewer/releases/latest`
   - Privacy policy: `https://github.com/n0va39/ComfyUI-EXIF-viewer/blob/main/PRIVACY.md`
   - Code signing policy: `https://github.com/n0va39/ComfyUI-EXIF-viewer/blob/main/CODE_SIGNING_POLICY.md`

Do not state that an existing release is signed. Releases published before SignPath acceptance are unsigned unless their Authenticode signature proves otherwise.

## After acceptance

1. Install the SignPath GitHub App for this repository and configure GitHub.com as the trusted build system.
2. Create or select the SignPath project for this repository.
3. Add [`packaging/signpath-artifact-configuration.xml`](../packaging/signpath-artifact-configuration.xml) as the project's default artifact configuration.
4. Configure a release signing policy that:
   - uses the SignPath Foundation certificate;
   - requires GitHub origin verification;
   - accepts only this repository's release-tag workflow;
   - requires one manual approval for every signing request.
5. Create a SignPath API token for a CI user with submitter access only.
6. Configure the repository values below:

| Type | Name | Value |
| --- | --- | --- |
| Actions secret | `SIGNPATH_API_TOKEN` | Submitter-only API token |
| Actions variable | `SIGNPATH_ORGANIZATION_ID` | SignPath organization ID |
| Actions variable | `SIGNPATH_PROJECT_SLUG` | Approved project slug |
| Actions variable | `SIGNPATH_SIGNING_POLICY_SLUG` | Manual-approval release policy slug |

Never store the API token in a repository variable, workflow file, release note or local config committed to Git.

## Signed release candidate

The [`Signed Release Candidate`](../.github/workflows/signed-release.yml) workflow runs only for `v*` tags. Before creating a tag:

1. Complete the planned code review and merge all release changes to `main`.
2. Confirm required checks and code scanning are successful on the current `main` commit.
3. Update `APP_VERSION` and `packaging/version_info.txt` to the same release version.
4. Confirm the SignPath project, artifact configuration, signing policy, GitHub App, secret and variables are configured.
5. Create the version tag on the current `main` commit and push it.
6. Review the signing request in SignPath and approve it manually.

The workflow verifies the signed executable and uploads a release-candidate artifact. It does not create or publish a GitHub Release. Release publication should occur only after downloading and smoke-testing that signed candidate.
