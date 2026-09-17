# CHANGELOG

## Unreleased
- reusable_go_lint_test: `go-private-repos-authentication` now mints a per-job GitHub App installation token (`tbv-protocol-deps-ro`, Contents: read, scoped by the new `private_repos` input, revoked when the job ends) instead of reading the org `GO_PRIVATE_TOKEN` PAT; the git URL rewrite is limited to `https://github.com/babylonlabs-io/` and uses the `x-access-token` form. Same caller requirements as the docker pipeline (org variable `TBV_DEPS_APP_ID` + secret `TBV_DEPS_APP_PRIVATE_KEY` visible to the caller, `secrets: inherit`). No caller currently enables the input.
- ci: run ECR publication regression scenarios on PRs changing the Docker workflow or its tests, using a read-only hosted job with mocked AWS/Docker commands.
- reusable_docker_pipeline: fail ECR publication when a platform or release tag already exists instead of silently accepting unverified registry content (baby-auditor-infra-findings#14). Lookup/API errors also fail; only an explicit `ImageNotFound` permits a push.
- Migration: rerunning publication with an existing immutable tag now fails, including identical rebuilds and partially completed earlier runs. Use a fresh `imageTag`; do not delete or make existing tags mutable to retry. This replaces the unverified ECR rerun skipping introduced in 0.20.1.
- Scope: this is a partial mitigation for #14. Caller-selected repository names and the existing shared ECR role remain supported, so an authorized caller can still reserve another service's unused tag. Builds now surface those collisions instead of reporting a successful publication; this does not establish deployment provenance.

## 0.21.0
- reusable_docker_pipeline: **breaking** — private build-time dependencies are fetched with a per-job GitHub App installation token (`tbv-protocol-deps-ro`, Contents: read, revoked when the job ends) instead of the org `PRIVATE_REPO_TOKEN` / `GO_PRIVATE_TOKEN` PATs; there is no PAT fallback. Callers that enable `private-repos-authentication` or `go-private-repos-authentication` must be able to read the org variable `TBV_DEPS_APP_ID` and secret `TBV_DEPS_APP_PRIVATE_KEY` (`secrets: inherit`) and should pass the new `private_repos` input (comma-separated repositories the token may read; empty = every repository the App is installed on). The BuildKit secret ids (`PRIVATE_REPO_TOKEN`, `GO_PRIVATE_TOKEN`) are unchanged, so Dockerfiles need no change.
- release: v0.21.0 is the first tag after v0.19.1 and also ships the 0.19.2, 0.20.0 and 0.20.1 entries below.

## 0.20.1
- reusable_docker_pipeline: resolve `IMAGE_TAG` to the ref name on tag events so a tag push no longer collides with the `<sha>`-named intermediate tags left by an earlier branch build of the same commit
- reusable_docker_pipeline: skip ECR pushes when the target tag already exists, making re-runs idempotent against ECR tag immutability

## 0.20.0
- security: **breaking** — remove `reusable_node_lint_test.yml` (BPT-056; instance of BPT-049). The workflow ran unpinned `npm install` and `npx semantic-release` in a job with `id-token: write`, `contents: write`, and `pull-requests: write`. It is no longer consumed: all former callers are archived or migrated to `babylon-toolkit`'s own release pipeline, and the last non-archived caller (`btc-staking-ts`) is scheduled for archival. Migration: repos still pinning historical tags for Node CI should move to the `babylon-toolkit` pipeline; the removed workflow remains reachable via tags ≤ v0.19.x and must not be adopted by new repos.

## 0.19.2
- ci: `reusable_check_pinned_actions` accepts `# unversioned` sentinel comment to allow SHA-only pinning for actions whose maintainers don't publish release tags (e.g. `dtolnay/rust-toolchain`). The SHA must still be a 40-char hex string; remote tag resolution is skipped.

## 0.19.0
- security: pin all GitHub Actions to full commit SHAs across all reusable workflows
- ci: add `reusable_check_pinned_actions` workflow — verifies all `uses:` entries are pinned to commit SHAs and match the remote tag via the GitHub API; supports a skip list for orgs with IP allowlists (e.g. `aquasecurity`)
- ci: upgrade `actions/checkout` from v4 to v6.0.2 across all workflows to resolve Node.js 20 deprecation warning

## 0.18.2
- security: add explicit `permissions: {}` top-level blocks and least-privilege job-level permissions to `reusable_changelog_reminder`, `reusable_go_lint_test`, `reusable_github_release`, and `reusable_sync_branch`

## 0.18.1
- reusable_docker_pipeline: bump trivy-action to v0.35.0
- reusable_docker_pipeline: fix `buildArtifactPrefix` not applied to intermediate registry tags

## 0.18.0
- reusable_docker_pipeline: push the scanned image to registry instead of rebuilding a new one
- reusable_docker_pipeline: use image tag instead of digest when pushing images
- reusable_docker_pipeline: remove ECR repository auto-creation

## 0.17.1
- Bump trivy-action version

## 0.17.0
- Allow disabling arm64 Docker build

## 0.16.1
- Re-add dockerfile_lint as a dependency of docker_build to block image publishing on lint failures

## 0.16.0
- Restructure reusable Docker pipeline to scan images with Trivy (filesystem and image scans) before pushing to registries, with results published to GitHub Security tab via SARIF upload

## 0.15.0
- Promote all changes from the 0.14.0–0.14.7 series to the 0.15.0 stable release (no additional code changes).

## 0.14.7
- Add permissions for NPM semantic release workflow to work with OIDC.

## 0.14.6
- Enable security-events permission to upload scan report

## 0.14.5
- Remove classic AWS token from docker workflow and add id-token perms to use OIDC

## 0.14.4
- Remove classic NPM token from node workflow and add id-token perms to use OIDC

## 0.14.3
- Enable creating ECR repo if it does not exist

## 0.14.2
- Enable private repos access through token env variable on docker pipeline

## 0.14.1
- Add inputs to reusable Docker pipeline to change the runner and timeout

## 0.14.0
- Add flags to reusable Docker pipeline for skipping ECR or Dockerhub pushes

## 0.13.5

- Add support for downloading/caching test file for unit tests
- Add codeowners
- Remove dockerfile_lint dependency from docker_build

## 0.13.4

- reusable_docker_pipeline: Publish only the tag with highest priority

## 0.13.2

- reusable_docker_pipeline: Disable latest tag

## 0.13.1

- Support semantic release in the node reusable pipeline

## 0.13.0

- Add reusable_github_release workflow

## 0.12.1

- reusable_docker_pipeline: Add buildArtifactPrefix to prevent uploading to the same artifact
- reusable_docker_pipeline: Ignore other tag patterns when inputs.imageTag is available

## 0.12.0

- reusable_docker_pipeline: Support custom image tag
- reusable_docker_pipeline: Support build-args

## 0.11.2

- reusable_docker_pipeline: Fix bug where a repo has both docker build workflow runs at the same time

## 0.11.1

- Bugfix: reusable_node_lint_test: Use changesets action to create pull request for release
- Bugfix: reusable_node_lint_test: Fix bug where both publish and run-changesets cannot be true

## 0.11.0

- reusable_node_lint_test: Support changesets release action

## 0.10.2

- Add inputs to allow Trivy failing: trivy_failable
- Add inputs to allow Hadolint failing: hadolint_failable

## 0.10.1

- Enable .trivyignore usage

## 0.10.0

- reusable_docker_pipeline: Add Trivy and Hadolint scanning

## 0.9.0

- reusable_node_lint_test: Support publishing a package

## 0.8.0

- reusable_docker_pipeline: Set default values for AWS_ECR_REGISTRY_ID and DOCKERHUB_REGISTRY_ID
- add backport release pipeline

## 0.7.0

- reusable_changelog_reminder: Add changelog reminder to remind PR submitters to update changelog
- reusable_go_releaser: Add go releaser to create a release upon a tagged version

## 0.6.0

- reusable_go_lint_test: Add Gosec job to inspects source code for security problems

## 0.5.0

- reusable_docker_pipeline: Add `platforms` input to support Multi-platform image
- reusable_docker_pipeline: Refactor docker_build job

## 0.4.0

- reusable_go_lint_test: Add go-private-repos input, allowing access to private repositories

## 0.3.2

- reusable_go_lint_test: Allow build job to be optional

## 0.3.1

- reusable_go_lint_test: Install dependencies on all steps
- reusable_go_lint_test: Templatize auth and make it optional

## 0.3.0

- Add reusable_node_lint_test workflow

## 0.2.0

- Add reusable_sync_branch workflow

## 0.1.0

- Add reusable_go_lint_test workflow
- Add resuable_docker_pipeline workflow
