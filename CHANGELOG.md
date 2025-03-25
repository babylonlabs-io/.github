# CHANGELOG

## 0.12.1

- reusable_docker_pipeline: Add buildArtifactPrefix to prevent uploading to the same artifact
- reusable_docker_pipeline: Only use one tag when publishing
  
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
