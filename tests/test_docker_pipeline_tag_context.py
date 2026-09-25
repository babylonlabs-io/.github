"""Exercise the image-tag and Docker context/Dockerfile validation of
reusable_docker_pipeline.yml without Docker or credentials.

The shell blocks are extracted from the workflow at test time, so the tests run
the code that ships. Run: python3 -m unittest discover -s tests -v
(requires bash, jq, and yq v4 or ruby to parse the workflow).
"""
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import unittest

from test_docker_pipeline_runner_inputs import WORKFLOW, load_workflow

SHA = '0123456789abcdef0123456789abcdef01234567'
OUT = '${{ needs.prepare-metadata.outputs.%s }}'
# github.run_id / github.run_attempt of the run under test: GitHub sets them,
# a caller cannot. Run ids are currently 11 digits and only grow.
RUN_ID = '12345678901'
RUN_ATTEMPT = '1'
RUN_SCOPE = f'-r{RUN_ID}.{RUN_ATTEMPT}'
# The registry tag limit, the two platform suffixes and what is left for the
# caller's tag once the run scope and the suffix are appended.
TAG_LIMIT = 128
PLATFORM_SUFFIXES = ('-linux-amd64', '-linux-arm64')
MAX_TAG = TAG_LIMIT - len(RUN_SCOPE) - max(len(suffix) for suffix in PLATFORM_SUFFIXES)

# What callers pass today (gh search code --owner babylonlabs-io, default
# branches): every explicit imageTag is the commit SHA or a git tag name plus a
# fixed suffix, or empty.
CALLER_IMAGE_TAGS = [
    SHA + '-ops',              # baby-watchtower
    SHA + '-test-utils',       # btc-vault
    SHA + '-backend',          # vaults-load-test-framework
    SHA + '-frontend',
    'v4.0.0-rc.1-testnet',     # babylon: ${{ github.ref_name }}-testnet (tag events only)
    'v1.2.3',                  # ${{ startsWith(github.ref, 'refs/tags/') && github.ref_name || '' }}
]
# Shapes of the git tags that exist in the caller repositories.
REAL_GIT_TAGS = [
    'v1.2.3', 'v0.0.1-snapshot-with-checkpoint', 'euphrates-0.4.0.rc.1', 'master-64d366f',
    'hwm-devnet-0-launch-ad8ebfd2', 'devnet-2025-11-07', 'hwm-uat-v0.6.0-alpha.1', 'latest', '0.1.0',
]
# Git tags with "/" exist (covenant-emulator, vault-provers) but never reach
# this workflow: every caller filters tags with '*' or 'v*', which do not match
# "/". They are rejected instead of being rewritten to "covenant-signer-v0.3.0".
SLASH_GIT_TAGS = ['covenant-signer/v0.3.0', 'circuits/v1.3', 'audit-fixes/sherlock-v0.4.0']
# Branch-type refs: the branch name is never used, the tag is the commit SHA.
BRANCH_REF_NAMES = ['main', 'release/v4.x', 'feat/foo', '123/merge',
                    'dependabot/go_modules/github.com/cosmos/cosmos-sdk-0.50.14', 'Feat/Foo']

# Pairs the previous sanitizer (sed 's|[^A-Za-z0-9_.-]|-|g; s|^[.-]+||') mapped
# to one tag. The first pair is the baby-auditor-infra-findings#121 example.
LOSSY_PAIRS = [
    ('.release-a', 'release-a'),
    ('feat/a-b', 'feat-a/b'),
    ('feat/a-b', 'feat-a-b'),
    ('-v1.0.0', 'v1.0.0'),
    ('v1.0.0+build', 'v1.0.0-build'),
    ('v1.0.0 rc1', 'v1.0.0-rc1'),
    ('release/v4.x', 'release-v4.x'),
    ('..--v1', 'v1'),
    ('v1@sha256', 'v1:sha256'),
]
HOSTILE_TAGS = [
    'v1\n', '\nv1', 'v1\nIMAGE_TAG=evil', 'v1\r', 'v1\t', 'v1\x1b[0m', 'v1\x7f',
    ' ', ' v1', 'v1 ', '.', '-', '.v1', '-v1', '--help', 'a/b', 'a\\b', 'a:b', 'a@b', 'a+b', 'a#b',
    'v1@sha256:' + 'a' * 64, 'repo:v1', '*', 'v*', '?', '[a]', '$(id)', '`id`', '${HOME}', '"v1"', "'v1'",
    'v1;id', 'v1|id', 'v1&', 'caf\u00e9', '\uff561', 'v\u0131',
    'a' * 117, 'a' * 128, 'a' * 129, 'a' * 4096,
    # Reserved: these are the per-platform tags of "v1".
    'v1-linux-amd64', 'v1-linux-arm64', SHA + '-linux-amd64',
    # baby-auditor-infra-findings#120: the staging tags of final tag "release".
    'release-linux-amd64', 'release-linux-arm64',
    'release' + RUN_SCOPE + '-linux-amd64', 'release' + RUN_SCOPE + '-linux-arm64',
]

# (input, expected output). Left column: every distinct value callers pass
# today for dockerContext / dockerfile, plus the input defaults.
CALLER_CONTEXTS = [('.', '.'), ('./', '.'), ('./agent', './agent'), ('./adapters/discord', './adapters/discord')]
CALLER_DOCKERFILES = [
    ('Dockerfile', './Dockerfile'), ('./Dockerfile', './Dockerfile'),
    ('./docker/ponder.Dockerfile', './docker/ponder.Dockerfile'),
    ('./docker/arbitrageur.Dockerfile', './docker/arbitrageur.Dockerfile'),
    ('./agent/Dockerfile', './agent/Dockerfile'), ('./adapters/discord/Dockerfile', './adapters/discord/Dockerfile'),
    ('./covenant-signer/Dockerfile', './covenant-signer/Dockerfile'),
    ('./backend/Dockerfile', './backend/Dockerfile'), ('./frontend/Dockerfile', './frontend/Dockerfile'),
    ('./contrib/images/babylond/Dockerfile', './contrib/images/babylond/Dockerfile'),
    ('./contrib/images/babylon-staking-indexer/Dockerfile', './contrib/images/babylon-staking-indexer/Dockerfile'),
    ('./contrib/images/local-bcd/Dockerfile', './contrib/images/local-bcd/Dockerfile'),
    ('./contrib/images/llm/Dockerfile', './contrib/images/llm/Dockerfile'),
]
EQUIVALENT_SPELLINGS = [('a/b', './a/b'), ('./a//b/', './a/b'), ('a/./b', './a/b'), ('././a', './a'),
                        ('vendor/github.com/x', './vendor/github.com/x'), ('a..b/c', './a..b/c'), ('.hidden/x', './.hidden/x')]
HOSTILE_PATHS = [
    # baby-auditor-infra-findings#119: mutable remote contexts.
    'https://github.com/attacker/repo.git#main', 'https://github.com/attacker/repo.git',
    'http://example.com/context.tar.gz', 'https://example.com/Dockerfile', 'git://github.com/attacker/repo',
    'git@github.com:attacker/repo.git', 'ssh://git@github.com/attacker/repo', 'github.com/attacker/repo',
    'github.com/attacker/repo#main', './github.com/attacker/repo', 'github.com', 'docker-image://alpine',
    'oci-layout://x', 'target:foo', '{{defaultContext}}', '{{defaultContext}}:sub',
    # Escapes from the checkout.
    '/', '/etc', '/home/runner/work', '..', '../', '../other', './..', 'a/..', 'a/../..', 'a/../../b', './a/../b',
    '~', '~/x', '$HOME', '${GITHUB_WORKSPACE}/..', 'C:\\x', 'a\\..\\b',
    # Options, globs, shell, whitespace, control characters, non-ASCII, size.
    '-', '-f', '--file=x', '--', '*', 'a*', '?', '$(id)', '`id`', 'a;b', 'a|b', 'a b', ' ', ' .', '. ',
    '', '.\n', '\n.', './a\nDOCKERFILE=https://evil', 'a\rb', 'a\tb', 'a\x1b[0m', 'caf\u00e9', 'a' * 256, 'a/' * 200,
]


class DockerPipelineTagAndContext(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.jobs = load_workflow()['jobs']
        cls.bash = shutil.which('bash')
        cls.tools = {name: shutil.which(name) for name in ('jq', 'xargs', 'tr', 'echo', 'cat')}
        if not cls.bash or not all(cls.tools.values()):
            raise RuntimeError('bash, jq, xargs, tr, echo and cat are required')

    # Per-platform digests the build legs record and the merge jobs consume (#47).
    DIGESTS = {'linux-amd64': 'sha256:' + '11' * 32, 'linux-arm64': 'sha256:' + '22' * 32}

    # Registry clients the merge jobs query before publishing. Defaults say the
    # final tag is absent; the env switches pick the answers a merge must refuse.
    REGISTRY_STUBS = {
        'aws': '''#!/bin/bash
# ecr batch-get-image
if [ -n "${AWS_GARBLED:-}" ]; then echo '{"images":[],"failures":[]}'; exit 0; fi
if [ "${TAG_EXISTS:-0}" = 1 ]; then echo '{"images":[{"imageId":{}}],"failures":[]}'; exit 0; fi
echo '{"images":[],"failures":[{"failureCode":"ImageNotFound","imageId":{"imageTag":"v1.2.3"}}]}'
''',
        'curl': '''#!/bin/bash
for a in "$@"; do case "$a" in *auth.docker.io*) echo '{"token":"stub"}'; exit 0 ;; esac; done
echo "${HTTP_CODE:-404}"
''',
    }

    def step(self, job, step_id=None, name=None):
        for step in self.jobs[job]['steps']:
            if (step_id and step.get('id') == step_id) or (name and step.get('name') == name):
                return step
        raise AssertionError(f'step not found: {job} {step_id or name}')

    def run_script(self, script, env, tools=(), cwd=None, stubs=None, digests=None):
        """Run a run: block the way the runner does (bash -e) with an empty
        $GITHUB_OUTPUT/$GITHUB_ENV, a mocked docker and no host PATH. The
        UTF-8 locale is what the hosted runners use.

        `stubs` adds extra executables (registry clients); `digests` writes the
        per-platform digest files the merge jobs read out of $RUNNER_TEMP."""
        with tempfile.TemporaryDirectory() as directory:
            directory = Path(directory)
            bin_dir = directory / 'bin'
            bin_dir.mkdir()
            docker = bin_dir / 'docker'
            docker.write_text('#!/bin/bash\nprintf "%s\\n" "$*" >> "$DOCKER_LOG"\n')
            docker.chmod(0o755)
            for tool in tools:
                (bin_dir / tool).symlink_to(self.tools[tool])
            for name, body in (stubs or {}).items():
                stub = bin_dir / name
                stub.write_text(body)
                stub.chmod(0o755)
            runner_temp = directory / 'runner-temp'
            runner_temp.mkdir()
            if digests is not None:
                digest_dir = runner_temp / 'digests'
                digest_dir.mkdir()
                for pair, digest in digests.items():
                    (digest_dir / pair).write_text(digest)
            output, github_env = directory / 'output', directory / 'env'
            output.touch()
            github_env.touch()
            full_env = dict(PATH=str(bin_dir), GITHUB_OUTPUT=str(output), GITHUB_ENV=str(github_env),
                            LANG='C.UTF-8', LC_ALL='C.UTF-8', DOCKER_LOG=str(directory / 'docker.log'),
                            RUNNER_TEMP=str(runner_temp))
            full_env.update(env)
            result = subprocess.run([self.bash, '--noprofile', '--norc', '-e', '-c', script],
                                    env=full_env, text=True, capture_output=True, cwd=cwd)
            log = directory / 'docker.log'
            return result, output.read_text(), github_env.read_text(), log.read_text() if log.exists() else ''

    def assert_rejected(self, result, output, github_env, docker_calls):
        self.assertNotEqual(result.returncode, 0, 'value must be rejected')
        self.assertIn('::error::', result.stdout)
        # Nothing reaches the job outputs.
        self.assertEqual(output, '')
        self.assertEqual(github_env, '')
        self.assertEqual(docker_calls, '')

    # --- #121: the image tag is used as given or rejected ----------------------

    def image_tag(self, image_tag='', ref_type='branch', ref_name='main', sha=SHA,
                  run_id=RUN_ID, run_attempt=RUN_ATTEMPT):
        step = self.step('prepare-metadata', step_id='set_image_tag')
        self.assertEqual(set(step['env']), {'INPUT_IMAGE_TAG', 'REF_TYPE', 'REF_NAME', 'GIT_SHA'})
        return self.run_script(step['run'], dict(INPUT_IMAGE_TAG=image_tag, REF_TYPE=ref_type,
                                                 REF_NAME=ref_name, GIT_SHA=sha,
                                                 GITHUB_RUN_ID=run_id, GITHUB_RUN_ATTEMPT=run_attempt))

    def accepted_outputs(self, **kwargs):
        """(tag, platform tag prefix), or (None, None) when the value is rejected."""
        result, output, github_env, docker_calls = self.image_tag(**kwargs)
        if result.returncode != 0:
            self.assert_rejected(result, output, github_env, docker_calls)
            return None, None
        match = re.fullmatch(r'IMAGE_TAG=([^\n]*)\nPLATFORM_TAG_PREFIX=([^\n]*)\n', output)
        self.assertIsNotNone(match, output)
        self.assertEqual(github_env + docker_calls, '')
        return match.group(1), match.group(2)

    def accepted_tag(self, **kwargs):
        """The emitted tag, or None when the value is rejected."""
        return self.accepted_outputs(**kwargs)[0]

    def test_canonical_tags_are_used_unchanged(self):
        canonical = CALLER_IMAGE_TAGS + REAL_GIT_TAGS + ['_x', 'A', 'Release-A', '1', 'a' * MAX_TAG, 'v1-linux-amd64-x',
                                                          'linux-amd64', 'v1.linux-amd64', 'a..b', 'a--b', 'a.-_']
        for tag in canonical:
            with self.subTest(source='imageTag', tag=tag):
                self.assertEqual(self.accepted_tag(image_tag=tag), tag)
            with self.subTest(source='git tag', tag=tag):
                self.assertEqual(self.accepted_tag(ref_type='tag', ref_name=tag), tag)

    def test_default_tag_for_real_refs(self):
        # Branch pushes, PR merge refs and dependabot branches publish the commit
        # SHA; the branch name (with its "/") is never part of the tag.
        for ref_name in BRANCH_REF_NAMES:
            with self.subTest(ref_name=ref_name):
                self.assertEqual(self.accepted_tag(ref_type='branch', ref_name=ref_name), SHA)
        self.assertEqual(self.accepted_tag(ref_type='tag', ref_name='v1.2.3'), 'v1.2.3')
        # imageTag wins over the ref; '' (the callers' non-tag branch of
        # startsWith(github.ref, 'refs/tags/') && github.ref_name || '') falls back.
        self.assertEqual(self.accepted_tag(image_tag=SHA + '-ops', ref_type='tag', ref_name='v1.2.3'), SHA + '-ops')
        self.assertEqual(self.accepted_tag(image_tag='', ref_type='branch', ref_name='feat/foo'), SHA)
        for ref_name in SLASH_GIT_TAGS:
            with self.subTest(ref_name=ref_name):
                self.assertIsNone(self.accepted_tag(ref_type='tag', ref_name=ref_name))
                self.assertIsNone(self.accepted_tag(image_tag=ref_name + '-testnet', ref_type='tag', ref_name=ref_name))

    @staticmethod
    def previous_sanitizer(raw):
        return re.sub(r'^[.-]+', '', re.sub(r'[^A-Za-z0-9_.-]', '-', raw))

    def test_names_the_previous_sanitizer_merged_stay_distinct(self):
        for first, second in LOSSY_PAIRS:
            with self.subTest(first=first, second=second):
                self.assertNotEqual(first, second)
                self.assertEqual(self.previous_sanitizer(first), self.previous_sanitizer(second),
                                 'fixture is not a collision of the previous sanitizer')
                for kwargs in (dict(image_tag='{}'), dict(ref_type='tag', ref_name='{}')):
                    emitted = []
                    for raw in (first, second):
                        tag = self.accepted_tag(**{key: value.replace('{}', raw) for key, value in kwargs.items()})
                        if tag is not None:
                            # Accepted means unchanged, so distinct inputs stay distinct.
                            self.assertEqual(tag, raw)
                            emitted.append(tag)
                    self.assertEqual(len(emitted), len(set(emitted)))
                    self.assertLess(len(emitted), 2, 'at most one name of a colliding pair is canonical')

    def test_case_variants_are_distinct_tags(self):
        # Registry tags are case-sensitive and the value is not lowercased.
        self.assertEqual(self.accepted_tag(image_tag='Release-A'), 'Release-A')
        self.assertEqual(self.accepted_tag(image_tag='release-a'), 'release-a')

    def test_long_names_are_rejected_not_truncated(self):
        # The grammar caps the tag at 116 = 128 - len('-linux-amd64'); the run
        # scope of the platform tags takes the rest. Over-long names are
        # rejected, never shortened into another publication's name.
        self.assertEqual(116 + len('-linux-amd64'), TAG_LIMIT)
        self.assertEqual(self.accepted_tag(image_tag='a' * MAX_TAG), 'a' * MAX_TAG)
        for first, second in (('a' * (MAX_TAG + 1), 'a' * (MAX_TAG + 2)),
                              ('a' * 117, 'a' * 118), ('a' * 128 + 'x', 'a' * 128 + 'y')):
            self.assertIsNone(self.accepted_tag(image_tag=first))
            self.assertIsNone(self.accepted_tag(image_tag=second))

    def test_platform_tags_always_fit_the_registry_limit(self):
        # The budget follows the run scope: a longer run id leaves less room,
        # and what is accepted always fits with the longest platform suffix.
        for run_id, run_attempt in ((RUN_ID, RUN_ATTEMPT), ('1', '1'), ('9' * 20, '10')):
            scope = len(f'-r{run_id}.{run_attempt}')
            longest = TAG_LIMIT - scope - max(len(suffix) for suffix in PLATFORM_SUFFIXES)
            with self.subTest(run_id=run_id, run_attempt=run_attempt):
                tag, prefix = self.accepted_outputs(image_tag='a' * longest, run_id=run_id,
                                                    run_attempt=run_attempt)
                self.assertEqual(tag, 'a' * longest)
                for suffix in PLATFORM_SUFFIXES:
                    self.assertEqual(len(prefix + suffix), TAG_LIMIT)
                self.assertIsNone(self.accepted_tag(image_tag='a' * (longest + 1), run_id=run_id,
                                                    run_attempt=run_attempt))

    def test_an_unusable_run_scope_stops_the_build(self):
        # Without a trustworthy run scope there is no unique staging namespace.
        for run_id, run_attempt in (('', '1'), (RUN_ID, ''), ('', ''), ('abc', '1'), (RUN_ID, 'x'),
                                    (RUN_ID + '.1', '1'), (' ' + RUN_ID, '1'), (RUN_ID + '\n1', '1'),
                                    ('-1', '1'), (RUN_ID, '1 ')):
            with self.subTest(run_id=run_id, run_attempt=run_attempt):
                self.assert_rejected(*self.image_tag(image_tag='v1.2.3', run_id=run_id, run_attempt=run_attempt))

    def test_hostile_tags_are_rejected(self):
        for tag in HOSTILE_TAGS:
            with self.subTest(source='imageTag', tag=tag):
                self.assert_rejected(*self.image_tag(image_tag=tag))
            with self.subTest(source='git tag', tag=tag):
                self.assert_rejected(*self.image_tag(ref_type='tag', ref_name=tag))
        # A commit SHA that is not a tag is rejected too (never seen in practice).
        self.assert_rejected(*self.image_tag(sha='abc/def'))
        self.assert_rejected(*self.image_tag(sha=''))

    def test_tag_script_does_not_rewrite(self):
        script = self.step('prepare-metadata', step_id='set_image_tag')['run']
        for rewriting in ('sed', 'tr ', '${tag//', '${tag/', '${tag:', '${tag#', '${tag%', ',,', '^^', 'cut'):
            self.assertNotIn(rewriting, script)
        self.assertIn("tag_grammar='^[A-Za-z0-9_][A-Za-z0-9_.-]{0,115}$'", script)
        self.assertIn('LC_ALL=C', script)

    # --- #120: per-platform images live where no caller can name them ----------

    def platform_pairs(self):
        """The platform_pair values docker_build can really produce: the matrix
        built by prepare-metadata, run through the real "Prepare" step."""
        matrix_step = self.step('prepare-metadata', step_id='set_matrix')
        result, output, _, _ = self.run_script(
            matrix_step['run'], dict(DISABLE_ARM64='false', RUNS_ON_AMD64='ubuntu-24.04',
                                     RUNS_ON_ARM64='ubuntu-24.04-arm64'), tools=('jq',))
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        matrix = json.loads(re.search(r'^MATRIX=(.*)$', output, re.M).group(1))
        prepare = self.step('docker_build', step_id='prepare')
        pairs = []
        for entry in matrix['include']:
            result, output, _, _ = self.run_script(prepare['run'], dict(PLATFORM=entry['platform']))
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            pairs.append(re.fullmatch(r'platform_pair=([^\n]*)\n', output).group(1))
        return pairs

    def test_every_platform_the_matrix_can_build_is_reserved(self):
        # A platform the build can push but the validator does not reserve
        # would be a caller-selectable tag again.
        pairs = self.platform_pairs()
        self.assertEqual(pairs, ['linux-amd64', 'linux-arm64'])
        self.assertEqual(sorted('-' + pair for pair in pairs), sorted(PLATFORM_SUFFIXES))
        script = self.step('prepare-metadata', step_id='set_image_tag')['run']
        self.assertIn('PLATFORM_SUFFIXES=(%s)' % ' '.join('-' + pair for pair in pairs), script)

    def test_a_final_tag_can_never_be_another_runs_platform_tag(self):
        """baby-auditor-infra-findings#120: whatever this workflow accepts as a
        final tag, the platform tags it then pushes are refused as final tags —
        from the imageTag input and from a git tag name, in this run and in any
        other. Platform images therefore cannot take, or be taken by, an
        advertised release name."""
        pairs = self.platform_pairs()
        for tag in ['release', 'v1.2.3', 'latest', SHA] + CALLER_IMAGE_TAGS:
            accepted, prefix = self.accepted_outputs(image_tag=tag)
            self.assertEqual(accepted, tag)
            self.assertEqual(prefix, tag + RUN_SCOPE)
            for pair in pairs:
                staging = f'{prefix}-{pair}'
                with self.subTest(tag=tag, staging=staging):
                    self.assertLessEqual(len(staging), TAG_LIMIT)
                    # The finding's own payload: "release-linux-amd64" as a release name.
                    self.assertIsNone(self.accepted_tag(image_tag=f'{tag}-{pair}'))
                    self.assertIsNone(self.accepted_tag(ref_type='tag', ref_name=f'{tag}-{pair}'))
                    # And the run-scoped staging name itself, from either source
                    # and from any other run.
                    self.assertIsNone(self.accepted_tag(image_tag=staging))
                    self.assertIsNone(self.accepted_tag(ref_type='tag', ref_name=staging))
                    self.assertIsNone(self.accepted_tag(image_tag=staging, run_id='222', run_attempt='3'))

    def test_platform_tags_are_unique_per_run_and_per_tag(self):
        # Two publications never share a staging tag: the run scope separates
        # runs and re-runs, the tag separates concurrent calls of one run.
        seen = {}
        for tag in ('release', 'release-testnet', 'v1.2.3', 'v1.2.3-r5.1'):
            for run_id, run_attempt in ((RUN_ID, '1'), (RUN_ID, '2'), ('222', '1')):
                accepted, prefix = self.accepted_outputs(image_tag=tag, run_id=run_id, run_attempt=run_attempt)
                self.assertEqual(accepted, tag)
                self.assertEqual(prefix, f'{tag}-r{run_id}.{run_attempt}')
                for pair in self.platform_pairs():
                    staging = f'{prefix}-{pair}'
                    self.assertNotIn(staging, seen, f'{seen.get(staging)} and {(tag, run_id, run_attempt)} collide')
                    seen[staging] = (tag, run_id, run_attempt)
        self.assertEqual(len(seen), 4 * 3 * 2)

    def test_publication_steps_use_only_this_runs_platform_tags(self):
        # The platform legs stage under the run-scoped prefix...
        for step_name in ('Push to Docker Hub', 'Push to ECR'):
            step = self.step('docker_build', name=step_name)
            with self.subTest(step=step_name):
                self.assertEqual(step['env']['PLATFORM_TAG_PREFIX'], OUT % 'platform-tag-prefix')
                self.assertIn('${PLATFORM_TAG_PREFIX}-', step['run'])
                # The final tag never names a platform image.
                self.assertNotIn('${IMAGE_TAG}-', step['run'])
                # ...and record the digest the push returned (#47).
                self.assertIn('RepoDigests', step['run'])
                self.assertIn('sha256:[0-9a-f]{64}', step['run'])
        # ...and the merge jobs consume those digests, never a tag name, so a
        # staging tag moved after the push cannot substitute the bytes (#47).
        for job in ('merge_dockerhub', 'merge_ecr'):
            step = self.step(job, name='Create manifest list and push')
            with self.subTest(job=job):
                self.assertNotIn('PLATFORM_TAG_PREFIX', step['env'])
                self.assertNotIn('${PLATFORM_TAG_PREFIX}', step['run'])
                self.assertIn('$RUNNER_TEMP/digests/', step['run'])
                self.assertIn('imagetools create -t "$first_tag" "${source_refs[@]}"', step['run'])
        # The prefix is produced by the credential-free validator, not recomputed.
        self.assertEqual(self.jobs['prepare-metadata']['outputs']['platform-tag-prefix'],
                         '${{ steps.set_image_tag.outputs.PLATFORM_TAG_PREFIX }}')
        self.assertEqual(WORKFLOW.read_text().count('needs.prepare-metadata.outputs.platform-tag-prefix'), 2)

    def test_merge_jobs_publish_only_the_validated_tag(self):
        text = WORKFLOW.read_text()
        self.assertEqual(text.count('inputs.imageTag'), 1, 'imageTag is read only by the validation step')
        for job, registry_env, registry in (('merge_dockerhub', 'DOCKERHUB_REGISTRY', 'babylonlabs'),
                                            ('merge_ecr', 'AWS_ECR_REGISTRY_ID', '123456789012.dkr.ecr.ap-east-1.amazonaws.com')):
            meta = self.step(job, step_id='meta')
            self.assertEqual(meta['with']['tags'].strip(), 'type=raw,value=' + OUT % 'image-tag')
            step = self.step(job, name='Create manifest list and push')
            self.assertEqual(step['env']['IMAGE_TAG'], OUT % 'image-tag')
            matrix = json.dumps({'include': [{'platform': 'linux/amd64'}, {'platform': 'linux/arm64'}]})
            # metadata-action (or anything else) handing back another tag must stop the publication.
            for final in ('release-a', 'feat-a-b', 'latest', ''):
                with self.subTest(job=job, final=final):
                    env = {registry_env: registry, 'IMAGE_NAME': 'app', 'IMAGE_TAG': '.release-a',
                           'BUILD_MATRIX': matrix, 'DOCKERHUB_USERNAME': 'u', 'DOCKERHUB_TOKEN': 't',
                           'DOCKER_METADATA_OUTPUT_JSON': json.dumps({'tags': [f'{registry}/app:{final}'] if final else []})}
                    result, _, _, docker_calls = self.run_script(
                        step['run'], env, tools=('jq', 'cat', 'tr', 'echo'),
                        stubs=self.REGISTRY_STUBS, digests=self.DIGESTS)
                    self.assertNotEqual(result.returncode, 0)
                    self.assertEqual(docker_calls, '')
        # Matching tag: Docker Hub publishes the validated tag from the digests
        # the two platform legs recorded, not from their staging tag names.
        tag, _ = self.accepted_outputs(image_tag='v1.2.3')
        step = self.step('merge_dockerhub', name='Create manifest list and push')
        env = dict(DOCKERHUB_REGISTRY='babylonlabs', IMAGE_NAME='app', IMAGE_TAG=tag,
                   DOCKERHUB_USERNAME='u', DOCKERHUB_TOKEN='t', BUILD_MATRIX=matrix,
                   DOCKER_METADATA_OUTPUT_JSON=json.dumps({'tags': ['babylonlabs/app:v1.2.3', 'babylonlabs/app:latest']}))
        result, _, _, docker_calls = self.run_script(step['run'], env, tools=('jq', 'cat', 'tr', 'echo'),
                                                    stubs=self.REGISTRY_STUBS, digests=self.DIGESTS)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(docker_calls.split(), ['buildx', 'imagetools', 'create', '-t', 'babylonlabs/app:v1.2.3',
                                                'babylonlabs/app@' + self.DIGESTS['linux-amd64'],
                                                'babylonlabs/app@' + self.DIGESTS['linux-arm64']])

    def test_digest_artifacts_are_keyed_on_the_image_not_the_run_attempt(self):
        """Two calls of this workflow in one run build different images
        (aave-v4-bots, covenant-emulator and babylon-desk each call it twice), so
        the artifact name has to carry the image. It must not carry the run
        attempt, or re-running a failed merge job looks for artifacts the
        still-green build jobs never re-uploaded."""
        image = OUT % 'image-name'
        for registry, merge_job in (('dockerhub', 'merge_dockerhub'), ('ecr', 'merge_ecr')):
            upload = self.step('docker_build', name=f'Upload {"Docker Hub" if registry == "dockerhub" else "ECR"} platform digest')
            download = self.step(merge_job, name='Download platform digests')
            with self.subTest(registry=registry):
                name = upload['with']['name']
                self.assertIn(image, name, 'artifact name must distinguish the image')
                self.assertIn('platform_pair', name, 'and the platform')
                self.assertNotIn('run_attempt', name)
                self.assertNotIn('run_attempt', download['with']['pattern'])
                # A re-run of a build job re-uploads the same name.
                self.assertTrue(upload['with']['overwrite'])
                # The pattern must actually match what was uploaded.
                self.assertEqual(download['with']['pattern'], f'digests-{registry}-{image}-*')
                self.assertTrue(name.startswith(f'digests-{registry}-{image}-'))
        # The two registries never collide with each other.
        self.assertNotEqual(self.step('merge_dockerhub', name='Download platform digests')['with']['pattern'],
                            self.step('merge_ecr', name='Download platform digests')['with']['pattern'])

    def test_merge_refuses_a_final_tag_that_already_exists(self):
        """#47: an existing final tag is never treated as success. ECR is
        immutable, Docker Hub is not, so both have to refuse it — and a registry
        that will not answer is not evidence of absence either."""
        matrix = json.dumps({'include': [{'platform': 'linux/amd64'}, {'platform': 'linux/arm64'}]})
        cases = (
            ('merge_dockerhub', dict(DOCKERHUB_REGISTRY='babylonlabs', DOCKERHUB_USERNAME='u', DOCKERHUB_TOKEN='t'),
             'babylonlabs', dict(HTTP_CODE='200'), dict(HTTP_CODE='503')),
            ('merge_ecr', dict(AWS_ECR_REGISTRY_ID='123456789012.dkr.ecr.ap-east-1.amazonaws.com'),
             '123456789012.dkr.ecr.ap-east-1.amazonaws.com', dict(TAG_EXISTS='1'), dict(AWS_GARBLED='1')),
        )
        for job, registry_env, registry, exists, unusable in cases:
            step = self.step(job, name='Create manifest list and push')
            for label, extra in (('already published', exists), ('registry will not answer', unusable)):
                with self.subTest(job=job, case=label):
                    env = dict(IMAGE_NAME='app', IMAGE_TAG='v1.2.3', BUILD_MATRIX=matrix,
                               DOCKER_METADATA_OUTPUT_JSON=json.dumps({'tags': [f'{registry}/app:v1.2.3']}),
                               **registry_env, **extra)
                    result, _, _, docker_calls = self.run_script(
                        step['run'], env, tools=('jq', 'cat', 'tr', 'echo'),
                        stubs=self.REGISTRY_STUBS, digests=self.DIGESTS)
                    self.assertNotEqual(result.returncode, 0, result.stdout)
                    self.assertIn('::error::', result.stdout)
                    self.assertEqual(docker_calls, '', 'nothing may be published')

    def test_merge_refuses_a_missing_or_malformed_platform_digest(self):
        """#47: the manifest is only ever built from digests this run recorded."""
        matrix = json.dumps({'include': [{'platform': 'linux/amd64'}, {'platform': 'linux/arm64'}]})
        step = self.step('merge_ecr', name='Create manifest list and push')
        registry = '123456789012.dkr.ecr.ap-east-1.amazonaws.com'
        for label, digests in (('missing', {'linux-amd64': self.DIGESTS['linux-amd64']}),
                               ('malformed', dict(self.DIGESTS, **{'linux-arm64': 'not-a-digest'})),
                               ('none recorded', {})):
            with self.subTest(case=label):
                env = dict(AWS_ECR_REGISTRY_ID=registry, IMAGE_NAME='app', IMAGE_TAG='v1.2.3',
                           BUILD_MATRIX=matrix,
                           DOCKER_METADATA_OUTPUT_JSON=json.dumps({'tags': [f'{registry}/app:v1.2.3']}))
                result, _, _, docker_calls = self.run_script(
                    step['run'], env, tools=('jq', 'cat', 'tr', 'echo'),
                    stubs=self.REGISTRY_STUBS, digests=digests)
                self.assertNotEqual(result.returncode, 0, result.stdout)
                self.assertIn('::error::', result.stdout)
                self.assertEqual(docker_calls, '')

    # --- #119: context and Dockerfile are paths inside the checkout -------------

    def build_paths(self, context='.', dockerfile='Dockerfile'):
        step = self.step('prepare-metadata', step_id='set_build_paths')
        self.assertEqual(set(step['env']), {'INPUT_DOCKER_CONTEXT', 'INPUT_DOCKERFILE'})
        return self.run_script(step['run'], dict(INPUT_DOCKER_CONTEXT=context, INPUT_DOCKERFILE=dockerfile))

    def test_caller_paths_pass_and_are_normalized(self):
        for context, expected in CALLER_CONTEXTS + EQUIVALENT_SPELLINGS:
            with self.subTest(context=context):
                result, output, github_env, _ = self.build_paths(context=context)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                self.assertEqual(output, f'DOCKER_CONTEXT={expected}\nDOCKERFILE=./Dockerfile\n')
                self.assertEqual(github_env, '')
        for dockerfile, expected in CALLER_DOCKERFILES + EQUIVALENT_SPELLINGS:
            with self.subTest(dockerfile=dockerfile):
                result, output, _, _ = self.build_paths(dockerfile=dockerfile)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                self.assertEqual(output, f'DOCKER_CONTEXT=.\nDOCKERFILE={expected}\n')

    def test_emitted_paths_are_never_a_url_or_an_option(self):
        for value, _ in CALLER_CONTEXTS + CALLER_DOCKERFILES + EQUIVALENT_SPELLINGS:
            _, output, _, _ = self.build_paths(context=value)
            emitted = output.split('\n')[0].split('=', 1)[1]
            self.assertRegex(emitted, r'\A(\.|\./[A-Za-z0-9_.-]+(/[A-Za-z0-9_.-]+)*)\Z')
            self.assertNotIn('..', emitted.split('/'))

    def test_remote_and_escaping_paths_are_rejected(self):
        for value in HOSTILE_PATHS:
            with self.subTest(input='dockerContext', value=value):
                self.assert_rejected(*self.build_paths(context=value))
            with self.subTest(input='dockerfile', value=value):
                self.assert_rejected(*self.build_paths(dockerfile=value))
        for value in ('.', './', './/.'):
            with self.subTest(input='dockerfile', value=value):
                self.assert_rejected(*self.build_paths(dockerfile=value))

    def test_build_uses_only_the_validated_paths(self):
        text = WORKFLOW.read_text()
        for raw in ('inputs.dockerContext', 'inputs.dockerfile'):
            self.assertEqual(len(re.findall(re.escape(raw) + r'\b', text)), 1, f'{raw} is read only by the validation step')
        outputs = self.jobs['prepare-metadata']['outputs']
        self.assertEqual(outputs['image-tag'], '${{ steps.set_image_tag.outputs.IMAGE_TAG }}')
        self.assertEqual(outputs['docker-context'], '${{ steps.set_build_paths.outputs.DOCKER_CONTEXT }}')
        self.assertEqual(outputs['dockerfile'], '${{ steps.set_build_paths.outputs.DOCKERFILE }}')
        # Validation runs where there are no secrets, token permissions or environment.
        validator = self.jobs['prepare-metadata']
        self.assertEqual(validator['permissions'], {})
        self.assertNotIn('environment', validator)
        self.assertNotIn('secrets.', json.dumps(validator))

        builds = [(job, step) for job, body in self.jobs.items() for step in body['steps']
                  if step.get('uses', '').startswith('docker/build-push-action@')]
        self.assertEqual([job for job, _ in builds], ['docker_build'])
        build = builds[0][1]
        self.assertEqual(build['with']['context'], OUT % 'docker-context')
        self.assertEqual(build['with']['file'], OUT % 'dockerfile')
        lint = next(step for step in self.jobs['dockerfile_lint']['steps']
                    if step.get('uses', '').startswith('hadolint/'))
        self.assertEqual(lint['with']['dockerfile'], OUT % 'dockerfile')

        names = [step.get('name') for step in self.jobs['docker_build']['steps']]
        confine = self.step('docker_build', name='Confine Docker context to the checkout')
        self.assertNotIn('if', confine)
        self.assertNotIn('continue-on-error', confine)
        self.assertEqual(confine['env'], {'DOCKER_CONTEXT': OUT % 'docker-context', 'DOCKERFILE': OUT % 'dockerfile'})
        self.assertLess(names.index('Checkout repository'), names.index('Confine Docker context to the checkout'))
        self.assertLess(names.index('Confine Docker context to the checkout'), names.index('Build image for scanning'))
        for login in ('Login to Docker Hub', 'Configure AWS credentials', 'Mint dependency read token (GitHub App)'):
            self.assertLess(names.index('Confine Docker context to the checkout'), names.index(login))

    def test_symlinks_out_of_the_checkout_are_rejected(self):
        step = self.step('docker_build', name='Confine Docker context to the checkout')
        with tempfile.TemporaryDirectory() as directory:
            root = Path(os.path.realpath(directory))
            workspace, outside = root / 'work' / 'repo', root / 'outside'
            (workspace / 'agent').mkdir(parents=True)
            (workspace / 'contrib/images/app').mkdir(parents=True)
            outside.mkdir()
            for dockerfile in (workspace / 'Dockerfile', workspace / 'agent/Dockerfile',
                               workspace / 'contrib/images/app/Dockerfile', outside / 'Dockerfile'):
                dockerfile.write_text('FROM scratch\n')
            (workspace / 'inside-link').symlink_to('agent')
            (workspace / 'outside-link').symlink_to(outside)
            (workspace / 'root-link').symlink_to('/')
            (workspace / 'parent-link').symlink_to('..')
            (workspace / 'a-file').write_text('')
            # A sibling whose name only shares the workspace prefix.
            (root / 'work' / 'repo-evil').mkdir()
            (root / 'work' / 'repo-evil' / 'Dockerfile').write_text('FROM scratch\n')
            (workspace / 'prefix-link').symlink_to(root / 'work' / 'repo-evil')
            # Dockerfiles that are themselves symlinks: to a regular file outside
            # the workspace (PR #90 review), to one inside it, and a dangling one.
            (workspace / 'Dockerfile.outside').symlink_to(outside / 'Dockerfile')
            (workspace / 'Dockerfile.passwd').symlink_to('/etc/passwd')
            (workspace / 'Dockerfile.inside').symlink_to('agent/Dockerfile')
            (workspace / 'agent' / 'Dockerfile.up').symlink_to('../Dockerfile')
            (workspace / 'Dockerfile.dangling').symlink_to('nowhere')
            (workspace / 'contrib/images/link').symlink_to('app')

            def confine(context, dockerfile, workspace_env=str(workspace)):
                return self.run_script(step['run'], dict(GITHUB_WORKSPACE=workspace_env, DOCKER_CONTEXT=context,
                                                         DOCKERFILE=dockerfile), cwd=str(workspace))

            for context, dockerfile in (('.', './Dockerfile'), ('./agent', './agent/Dockerfile'),
                                        ('.', './contrib/images/app/Dockerfile'), ('./agent', './Dockerfile'),
                                        ('./contrib/images/app', './contrib/images/app/Dockerfile')):
                with self.subTest(context=context, dockerfile=dockerfile):
                    result, output, github_env, _ = confine(context, dockerfile)
                    self.assertEqual((result.returncode, output, github_env), (0, '', ''), result.stdout + result.stderr)
            for context, dockerfile in (('.', './Dockerfile.outside'), ('.', './Dockerfile.passwd'),
                                        ('.', './Dockerfile.inside'), ('./agent', './agent/Dockerfile.up'),
                                        ('.', './Dockerfile.dangling'),
                                        # Reached through a symlinked directory, target inside the repository.
                                        ('.', './inside-link/Dockerfile'), ('.', './contrib/images/link/Dockerfile'),
                                        ('./inside-link', './Dockerfile'), ('./contrib/images/link', './Dockerfile'),
                                        ('./inside-link', './inside-link/Dockerfile'),
                                        ('.', './a/../Dockerfile'), ('./agent/..', './Dockerfile'),
                                        ('./outside-link', './Dockerfile'), ('./root-link', './Dockerfile'),
                                        ('./parent-link', './Dockerfile'), ('./prefix-link', './Dockerfile'),
                                        ('.', './outside-link/Dockerfile'), ('.', './prefix-link/Dockerfile'),
                                        ('./missing', './Dockerfile'), ('./a-file', './Dockerfile'),
                                        ('.', './missing/Dockerfile'), ('.', './agent'), ('.', './a-file/Dockerfile'),
                                        ('', './Dockerfile'), ('.', '')):
                with self.subTest(context=context, dockerfile=dockerfile):
                    self.assert_rejected(*confine(context, dockerfile))
            # Second layer on its own: the symlink refusal above shadows it, so call
            # the shipped confine_dir function directly (the script up to its first
            # check, then one call) and prove it rejects by physical location.
            definitions, separator, _ = step['run'].partition('refuse_symlinks "Docker context" "$DOCKER_CONTEXT"\n')
            self.assertTrue(separator)
            self.assertIn('confine_dir() {', definitions)
            for directory_name, accepted in (('./agent', True), ('.', True), ('./inside-link', True),
                                             ('./outside-link', False), ('./prefix-link', False),
                                             ('./root-link', False), ('./parent-link', False), ('./missing', False)):
                with self.subTest(confine_dir=directory_name):
                    outcome = self.run_script(definitions + f'confine_dir "test" "{directory_name}"\n',
                                              dict(GITHUB_WORKSPACE=str(workspace), DOCKER_CONTEXT='.',
                                                   DOCKERFILE='./Dockerfile'), cwd=str(workspace))
                    if accepted:
                        self.assertEqual(outcome[0].returncode, 0, outcome[0].stdout + outcome[0].stderr)
                    else:
                        self.assert_rejected(*outcome)
            self.assert_rejected(*confine('.', './Dockerfile', workspace_env=''))
            self.assert_rejected(*confine('.', './Dockerfile', workspace_env='/'))


if __name__ == '__main__':
    unittest.main()
