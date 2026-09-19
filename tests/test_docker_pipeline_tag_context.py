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
        cls.tools = {name: shutil.which(name) for name in ('jq', 'xargs', 'tr', 'echo')}
        if not cls.bash or not all(cls.tools.values()):
            raise RuntimeError('bash, jq, xargs, tr and echo are required')

    def step(self, job, step_id=None, name=None):
        for step in self.jobs[job]['steps']:
            if (step_id and step.get('id') == step_id) or (name and step.get('name') == name):
                return step
        raise AssertionError(f'step not found: {job} {step_id or name}')

    def run_script(self, script, env, tools=(), cwd=None):
        """Run a run: block the way the runner does (bash -e) with an empty
        $GITHUB_OUTPUT/$GITHUB_ENV, a mocked docker and no host PATH. The
        UTF-8 locale is what the hosted runners use."""
        with tempfile.TemporaryDirectory() as directory:
            directory = Path(directory)
            bin_dir = directory / 'bin'
            bin_dir.mkdir()
            docker = bin_dir / 'docker'
            docker.write_text('#!/bin/bash\nprintf "%s\\n" "$*" >> "$DOCKER_LOG"\n')
            docker.chmod(0o755)
            for tool in tools:
                (bin_dir / tool).symlink_to(self.tools[tool])
            output, github_env = directory / 'output', directory / 'env'
            output.touch()
            github_env.touch()
            full_env = dict(PATH=str(bin_dir), GITHUB_OUTPUT=str(output), GITHUB_ENV=str(github_env),
                            LANG='C.UTF-8', LC_ALL='C.UTF-8', DOCKER_LOG=str(directory / 'docker.log'))
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

    def image_tag(self, image_tag='', ref_type='branch', ref_name='main', sha=SHA):
        step = self.step('prepare-metadata', step_id='set_image_tag')
        self.assertEqual(set(step['env']), {'INPUT_IMAGE_TAG', 'REF_TYPE', 'REF_NAME', 'GIT_SHA'})
        return self.run_script(step['run'], dict(INPUT_IMAGE_TAG=image_tag, REF_TYPE=ref_type,
                                                 REF_NAME=ref_name, GIT_SHA=sha))

    def accepted_tag(self, **kwargs):
        """The emitted tag, or None when the value is rejected."""
        result, output, github_env, docker_calls = self.image_tag(**kwargs)
        if result.returncode != 0:
            self.assert_rejected(result, output, github_env, docker_calls)
            return None
        match = re.fullmatch(r'IMAGE_TAG=([^\n]*)\n', output)
        self.assertIsNotNone(match, output)
        self.assertEqual(github_env + docker_calls, '')
        return match.group(1)

    def test_canonical_tags_are_used_unchanged(self):
        canonical = CALLER_IMAGE_TAGS + REAL_GIT_TAGS + ['_x', 'A', 'Release-A', '1', 'a' * 116, 'v1-linux-amd64-x',
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
        self.assertEqual(self.accepted_tag(image_tag='a' * 116), 'a' * 116)
        # 116 + len('-linux-amd64') == 128, the registry limit for the platform tags.
        self.assertEqual(116 + len('-linux-amd64'), 128)
        for first, second in (('a' * 117, 'a' * 118), ('a' * 128 + 'x', 'a' * 128 + 'y')):
            self.assertIsNone(self.accepted_tag(image_tag=first))
            self.assertIsNone(self.accepted_tag(image_tag=second))

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
                    env = {registry_env: registry, 'IMAGE_NAME': 'app', 'IMAGE_TAG': '.release-a', 'BUILD_MATRIX': matrix,
                           'DOCKER_METADATA_OUTPUT_JSON': json.dumps({'tags': [f'{registry}/app:{final}'] if final else []})}
                    result, _, _, docker_calls = self.run_script(step['run'], env, tools=('jq', 'xargs', 'tr', 'echo'))
                    self.assertNotEqual(result.returncode, 0)
                    self.assertEqual(docker_calls, '')
        # Matching tag: Docker Hub publishes from the two platform tags of that same tag.
        step = self.step('merge_dockerhub', name='Create manifest list and push')
        env = dict(DOCKERHUB_REGISTRY='babylonlabs', IMAGE_NAME='app', IMAGE_TAG='v1.2.3', BUILD_MATRIX=matrix,
                   DOCKER_METADATA_OUTPUT_JSON=json.dumps({'tags': ['babylonlabs/app:v1.2.3', 'babylonlabs/app:latest']}))
        result, _, _, docker_calls = self.run_script(step['run'], env, tools=('jq', 'xargs', 'tr', 'echo'))
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(docker_calls.split(), ['buildx', 'imagetools', 'create', '-t', 'babylonlabs/app:v1.2.3',
                                                'babylonlabs/app:v1.2.3-linux-amd64', 'babylonlabs/app:v1.2.3-linux-arm64'])

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

            def confine(context, dockerfile, workspace_env=str(workspace)):
                return self.run_script(step['run'], dict(GITHUB_WORKSPACE=workspace_env, DOCKER_CONTEXT=context,
                                                         DOCKERFILE=dockerfile), cwd=str(workspace))

            for context, dockerfile in (('.', './Dockerfile'), ('./agent', './agent/Dockerfile'),
                                        ('.', './contrib/images/app/Dockerfile'), ('./inside-link', './inside-link/Dockerfile')):
                with self.subTest(context=context, dockerfile=dockerfile):
                    result, output, github_env, _ = confine(context, dockerfile)
                    self.assertEqual((result.returncode, output, github_env), (0, '', ''), result.stdout + result.stderr)
            for context, dockerfile in (('./outside-link', './Dockerfile'), ('./root-link', './Dockerfile'),
                                        ('./parent-link', './Dockerfile'), ('./prefix-link', './Dockerfile'),
                                        ('.', './outside-link/Dockerfile'), ('.', './prefix-link/Dockerfile'),
                                        ('./missing', './Dockerfile'), ('./a-file', './Dockerfile'),
                                        ('.', './missing/Dockerfile'), ('.', './agent'), ('.', './a-file/Dockerfile'),
                                        ('', './Dockerfile'), ('.', '')):
                with self.subTest(context=context, dockerfile=dockerfile):
                    self.assert_rejected(*confine(context, dockerfile))
            self.assert_rejected(*confine('.', './Dockerfile', workspace_env=''))
            self.assert_rejected(*confine('.', './Dockerfile', workspace_env='/'))


if __name__ == '__main__':
    unittest.main()
