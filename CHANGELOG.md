# CHANGELOG

## Unreleased

- Add support for downloading/caching test file for unit tests
- Add codeowners

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
