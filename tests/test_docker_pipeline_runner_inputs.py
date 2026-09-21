"""Exercise the runner-label validation, matrix building and Docker credential
cleanup scripts of reusable_docker_pipeline.yml without Docker or credentials.

The shell blocks are extracted from the workflow at test time, so the tests run
the code that ships. Run: python3 -m unittest discover -s tests -v
(requires bash, jq, and yq v4 or ruby to parse the workflow).
"""
import json
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import unittest

WORKFLOW = Path(__file__).resolve().parents[1] / '.github/workflows/reusable_docker_pipeline.yml'

DEFAULT_AMD64 = 'ubuntu-24.04'
DEFAULT_ARM64 = 'ubuntu-24.04-arm64'
APPROVED_AMD64 = ['ubuntu-24.04', 'ubuntu-24.04-8core']
APPROVED_ARM64 = ['ubuntu-24.04-arm64']
ECR_REGISTRY = '123456789012.dkr.ecr.ap-east-1.amazonaws.com'

# baby-auditor-infra-findings#65: closes the JSON string, appends a matrix entry
# on the privileged DinD pool whose platform carries an escaped newline that
# became a second $GITHUB_ENV assignment (BASH_ENV) in the build job.
ISSUE_65_PAYLOAD = ('ubuntu-24.04-arm64"},{"platform":"linux/arm64\\nBASH_ENV=pwn.sh",'
                    '"runner":"arc-runner-set-dind')

HOSTILE_LABELS = [
    ISSUE_65_PAYLOAD,
    'arc-runner-set-dind', 'arc-runner-set', 'gpu', 'localnet', 'self-hosted',
    '["self-hosted","gpu"]', '["ubuntu-24.04"]',
    '', ' ', ' ubuntu-24.04', 'ubuntu-24.04 ', 'ubuntu-24.04\n', '\nubuntu-24.04',
    'ubuntu-24.04\narc-runner-set-dind', 'arc-runner-set-dind\nubuntu-24.04',
    'ubuntu-24.04\r', 'ubuntu-24.04\t', 'ubuntu-24.04\x1b[0m',
    'ubuntu-24.04\nBASH_ENV=pwn.sh', 'ubuntu-24.04\nMATRIX={"include":[]}',
    'ubuntu-latest', 'UBUNTU-24.04', 'ubuntu-24.04-8core-x', 'ubuntu-24.04*', '*', '?*',
    'ubuntu-24.04"', '$(id)', '`id`', '${HOME}', '-n',
]


def load_workflow():
    if shutil.which('yq'):
        raw = subprocess.check_output(['yq', '-o=json', str(WORKFLOW)])
    else:
        raw = subprocess.check_output(
            ['ruby', '-ryaml', '-rjson', '-e', 'puts JSON.generate(YAML.load_file(ARGV[0]))', str(WORKFLOW)])
    return json.loads(raw)


class DockerPipelineRunnerInputs(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.jobs = load_workflow()['jobs']
        cls.bash = shutil.which('bash')
        cls.tools = {name: shutil.which(name) for name in ('jq', 'mktemp', 'rm', 'cut')}
        if not cls.bash or not all(cls.tools.values()):
            raise RuntimeError('bash, jq, mktemp, rm and cut are required')

    def step(self, job, step_id=None, name=None):
        for step in self.jobs[job]['steps']:
            if (step_id and step.get('id') == step_id) or (name and step.get('name') == name):
                return step
        raise AssertionError(f'step not found: {job} {step_id or name}')

    def run_script(self, script, env, docker_exit=0):
        """Run a run: block the way the runner does (bash -e), with an empty
        $GITHUB_OUTPUT/$GITHUB_ENV, a mocked docker and no host PATH."""
        with tempfile.TemporaryDirectory() as directory:
            directory = Path(directory)
            bin_dir = directory / 'bin'
            bin_dir.mkdir()
            docker = bin_dir / 'docker'
            docker.write_text('#!/bin/bash\nprintf "%s\\n" "$*" >> "$DOCKER_LOG"\nexit "$DOCKER_EXIT"\n')
            docker.chmod(0o755)
            for tool, source in self.tools.items():
                (bin_dir / tool).symlink_to(source)
            output, github_env = directory / 'output', directory / 'env'
            output.touch()
            github_env.touch()
            full_env = dict(PATH=str(bin_dir), GITHUB_OUTPUT=str(output), GITHUB_ENV=str(github_env),
                            GITHUB_REPOSITORY='babylonlabs-io/example',
                            DOCKER_LOG=str(directory / 'docker.log'), DOCKER_EXIT=str(docker_exit))
            full_env.update(env)
            result = subprocess.run([self.bash, '--noprofile', '--norc', '-e', '-c', script],
                                    env=full_env, text=True, capture_output=True)
            log = directory / 'docker.log'
            return result, output.read_text(), github_env.read_text(), log.read_text() if log.exists() else ''

    def build_matrix(self, amd64=DEFAULT_AMD64, arm64=DEFAULT_ARM64, disable_arm64='false'):
        step = self.step('prepare-metadata', step_id='set_matrix')
        self.assertEqual(set(step['env']), {'DISABLE_ARM64', 'RUNS_ON_AMD64', 'RUNS_ON_ARM64'})
        return self.run_script(step['run'], dict(DISABLE_ARM64=disable_arm64, RUNS_ON_AMD64=amd64,
                                                 RUNS_ON_ARM64=arm64))

    # --- #65 / #55: matrix building and the runner allowlist -----------------

    def test_approved_labels_produce_the_expected_matrix(self):
        for amd64 in APPROVED_AMD64:
            for arm64 in APPROVED_ARM64:
                with self.subTest(amd64=amd64, arm64=arm64):
                    result, output, github_env, _ = self.build_matrix(amd64, arm64)
                    self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                    # Exact text: key order keeps the matrix job names stable.
                    self.assertEqual(output, (
                        'MATRIX={"include":[{"platform":"linux/amd64","runner":"%s"},'
                        '{"platform":"linux/arm64","runner":"%s"}]}\nRUNNER_AMD64=%s\n' % (amd64, arm64, amd64)))
                    self.assertEqual(github_env, '')

    def test_disable_arm64_builds_a_single_entry(self):
        result, output, _, _ = self.build_matrix('ubuntu-24.04-8core', disable_arm64='true')
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(output, 'MATRIX={"include":[{"platform":"linux/amd64","runner":"ubuntu-24.04-8core"}]}\n'
                                 'RUNNER_AMD64=ubuntu-24.04-8core\n')

    def test_hostile_and_unapproved_labels_are_rejected(self):
        for label in HOSTILE_LABELS + APPROVED_ARM64:
            for disable_arm64 in ('false', 'true'):
                with self.subTest(input='runs_on_amd64', label=label, disable_arm64=disable_arm64):
                    self.assert_rejected(*self.build_matrix(amd64=label, disable_arm64=disable_arm64))
        for label in HOSTILE_LABELS + APPROVED_AMD64:
            for disable_arm64 in ('false', 'true'):
                with self.subTest(input='runs_on_arm64', label=label, disable_arm64=disable_arm64):
                    self.assert_rejected(*self.build_matrix(arm64=label, disable_arm64=disable_arm64))

    def assert_rejected(self, result, output, github_env, docker_calls):
        self.assertNotEqual(result.returncode, 0, 'label must be rejected')
        self.assertIn('::error::', result.stdout)
        # Nothing reaches the job outputs: no matrix, no extra entry, no env line.
        self.assertEqual(output, '')
        self.assertEqual(github_env, '')
        self.assertEqual(docker_calls, '')

    def test_matrix_is_encoded_by_jq_not_string_concatenation(self):
        script = self.step('prepare-metadata', step_id='set_matrix')['run']
        self.assertRegex(script, r'jq -cn --arg amd64 "\$RUNS_ON_AMD64"')
        self.assertNotIn('MATRIX={', script)
        self.assertNotRegex(script, r'\\"runner\\"')

    def test_image_name_accepts_the_names_callers_pass(self):
        script = self.step('prepare-metadata', step_id='set_image_name')['run']
        # Every repoName passed by a caller today, plus the fallback.
        for name in ('babylond', 'vault-provers', 'vault-provers-bsn', 'babylon-desk-bridge',
                     'babylon-desk-discord-adapter', 'aave-bots-indexer', 'aave-bots-svc',
                     'a', 'A', '1', 'a_b', 'a.b', 'a' * 128):
            with self.subTest(name=name):
                result, output, github_env, _ = self.run_script(script, dict(REPO_NAME=name))
                self.assertEqual((result.returncode, output, github_env), (0, f'IMAGE_NAME={name}\n', ''))
        result, output, _, _ = self.run_script(script, dict(REPO_NAME=''))
        self.assertEqual((result.returncode, output), (0, 'IMAGE_NAME=example\n'))

    def test_image_name_rejects_hostile_repository_names(self):
        script = self.step('prepare-metadata', step_id='set_image_name')['run']
        for name in ('babylond\nMATRIX={"include":[]}', 'babylond\r', 'a\tb', 'a\x1b[0m',
                     # Another namespace, tag or digest smuggled into the reference.
                     'a/b', 'babylonlabs/babylond', '../babylond', 'a:b', 'a:v1', 'a@sha256:' + 'b' * 64,
                     # Shapes the registry or the shell would read as something else.
                     '-x', '--help', '.github', '.hidden', '_x', 'a b', ' a', 'a ', '*', 'a*', '$(id)',
                     '`id`', '${HOME}', '"a"', "'a'", 'a;id', 'a|id', 'café', 'a' * 129):
            with self.subTest(name=name):
                result, output, github_env, _ = self.run_script(script, dict(REPO_NAME=name))
                self.assertNotEqual(result.returncode, 0, name)
                self.assertIn('::error::', result.stdout)
                self.assertEqual(output + github_env, '')
        # The fallback is validated too: a repository name the registry rejects
        # must fail here, not after the credentials exist.
        result, output, _, _ = self.run_script(script, dict(REPO_NAME='', GITHUB_REPOSITORY='babylonlabs-io/.github'))
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(output, '')

    # --- #65: matrix values never reach $GITHUB_ENV ---------------------------

    def test_platform_pair_is_derived_from_fixed_strings(self):
        step = self.step('docker_build', step_id='prepare')
        for platform, pair in (('linux/amd64', 'linux-amd64'), ('linux/arm64', 'linux-arm64')):
            result, output, github_env, _ = self.run_script(step['run'], dict(PLATFORM=platform))
            self.assertEqual((result.returncode, output, github_env), (0, f'platform_pair={pair}\n', ''))
        for platform in ('linux/arm64\nBASH_ENV=pwn.sh', 'linux/amd64\n', 'linux/amd64\r', 'linux/arm64 ',
                         'linux/riscv64', 'linux/*', '*', ''):
            with self.subTest(platform=platform):
                result, output, github_env, _ = self.run_script(step['run'], dict(PLATFORM=platform))
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(output + github_env, '')

    def test_only_the_docker_config_step_writes_github_env(self):
        writers = {(job_name, step.get('name')) for job_name, job in self.jobs.items()
                   for step in job['steps'] if 'GITHUB_ENV' in step.get('run', '')}
        self.assertEqual(writers, {(job, 'Use a job-scoped Docker config') for job in self.login_jobs()})
        self.assertNotIn('env.PLATFORM_PAIR', WORKFLOW.read_text())
        for step_name in ('Push to Docker Hub', 'Push to ECR'):
            env = self.step('docker_build', name=step_name)['env']
            self.assertEqual(env['PLATFORM_PAIR'], '${{ steps.prepare.outputs.platform_pair }}')

    def test_no_expression_is_interpolated_into_a_run_block(self):
        for job_name, job in self.jobs.items():
            for step in job['steps']:
                self.assertNotIn('${{', step.get('run', ''), f"{job_name}: {step.get('name')}")

    # --- #55: jobs only run on validated labels --------------------------------

    def test_jobs_run_only_on_validated_runners(self):
        validator = self.jobs['prepare-metadata']
        # The validating job cannot itself run on a caller-selected runner and
        # holds no token permissions, secrets or environment.
        self.assertEqual(validator['runs-on'], 'ubuntu-24.04')
        self.assertEqual(validator['permissions'], {})
        self.assertNotIn('needs', validator)
        self.assertNotIn('environment', validator)
        self.assertNotIn('secrets.', json.dumps(validator))
        self.assertEqual(validator['outputs']['runner-amd64'], '${{ steps.set_matrix.outputs.RUNNER_AMD64 }}')
        self.assertEqual(validator['outputs']['matrix'], '${{ steps.set_matrix.outputs.MATRIX }}')
        for job_name, job in self.jobs.items():
            if job_name == 'prepare-metadata':
                continue
            with self.subTest(job=job_name):
                self.assertIn(job['runs-on'], ('${{ needs.prepare-metadata.outputs.runner-amd64 }}',
                                               '${{ matrix.runner }}'))
                self.assertIn('prepare-metadata', job['needs'])
                if job['runs-on'] == '${{ matrix.runner }}':
                    self.assertEqual(job['strategy']['matrix'],
                                     '${{ fromJSON(needs.prepare-metadata.outputs.matrix) }}')
        # The raw inputs are read in exactly one place: the validation step env.
        self.assertEqual(re.findall(r'inputs\.runs_on_\w+', WORKFLOW.read_text()),
                         ['inputs.runs_on_amd64', 'inputs.runs_on_arm64'])

    def test_input_defaults_are_approved(self):
        inputs = load_workflow()
        inputs = next(value for key, value in inputs.items() if key in ('on', True, 'true'))['workflow_call']['inputs']
        self.assertIn(inputs['runs_on_amd64']['default'], APPROVED_AMD64)
        self.assertIn(inputs['runs_on_arm64']['default'], APPROVED_ARM64)

    # --- #61: registry logins are job-scoped and always removed ----------------

    def login_jobs(self):
        def logs_in(step):
            return step.get('uses', '').startswith('docker/login-action@') or 'docker login' in step.get('run', '')
        return [name for name, job in self.jobs.items() if any(logs_in(step) for step in job['steps'])]

    def test_every_login_job_scopes_and_removes_its_docker_config(self):
        self.assertEqual(sorted(self.login_jobs()), ['docker_build', 'merge_dockerhub', 'merge_ecr'])
        for job_name in self.login_jobs():
            steps = self.jobs[job_name]['steps']
            with self.subTest(job=job_name):
                # First, so setup-buildx and every login see DOCKER_CONFIG; last
                # and unconditional, so no later step can log in again.
                self.assertEqual(steps[0]['name'], 'Use a job-scoped Docker config')
                self.assertNotIn('if', steps[0])
                self.assertEqual(steps[-1]['name'], 'Remove registry credentials')
                self.assertEqual(steps[-1].get('if'), 'always()')
                self.assertNotIn('continue-on-error', steps[-1])
                self.assertEqual(steps[-1]['env'], {'AWS_ECR_REGISTRY_ID': '${{ vars.AWS_ECR_REGISTRY_ID }}'})
                self.check_docker_config_lifecycle(steps[0]['run'], steps[-1]['run'])

    def check_docker_config_lifecycle(self, setup, cleanup):
        with tempfile.TemporaryDirectory() as runner_temp, tempfile.TemporaryDirectory() as home:
            result, _, github_env, _ = self.run_script(setup, dict(RUNNER_TEMP=runner_temp, HOME=home))
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            exported = dict(line.split('=', 1) for line in github_env.splitlines())
            self.assertEqual(set(exported), {'DOCKER_CONFIG', 'BUILDX_CONFIG'})
            docker_config = Path(exported['DOCKER_CONFIG'])
            self.assertEqual(docker_config.parent, Path(runner_temp))
            self.assertTrue(docker_config.is_dir())
            self.assertEqual(docker_config.stat().st_mode & 0o077, 0)
            self.assertNotEqual(exported['BUILDX_CONFIG'], exported['DOCKER_CONFIG'])
            self.assertEqual(Path(exported['BUILDX_CONFIG']).parent, Path(runner_temp))

            for docker_exit in (1, 0):  # a failing logout must not keep the credentials on disk
                docker_config.mkdir(exist_ok=True)
                (docker_config / 'config.json').write_text('{"auths":{"%s":{"auth":"QVdTOnBhc3M="}}}' % ECR_REGISTRY)
                result, _, _, calls = self.run_script(
                    cleanup, dict(RUNNER_TEMP=runner_temp, HOME=home, DOCKER_CONFIG=str(docker_config),
                                  AWS_ECR_REGISTRY_ID=ECR_REGISTRY), docker_exit=docker_exit)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                self.assertEqual(calls, f'logout {ECR_REGISTRY}\nlogout\n')
                self.assertFalse(docker_config.exists())

            # Never delete anything but the directory this job created.
            user_config = Path(home) / '.docker'
            user_config.mkdir()
            # Temporary paths only: a broken guard must not be able to delete real data.
            for foreign in (str(user_config), home, runner_temp, runner_temp + '/docker-config',
                            runner_temp + '/other/docker-config.abc',
                            runner_temp + '/docker-config.abc/../../' + Path(home).name,
                            runner_temp + '/docker-config.abc/..'):
                result, _, _, calls = self.run_script(
                    cleanup, dict(RUNNER_TEMP=runner_temp, HOME=home, DOCKER_CONFIG=foreign,
                                  AWS_ECR_REGISTRY_ID=ECR_REGISTRY))
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(calls, '')
                self.assertTrue(user_config.is_dir())

            # The job failed before the config step: nothing to remove, no error.
            result, _, _, calls = self.run_script(cleanup, dict(RUNNER_TEMP=runner_temp, HOME=home,
                                                                AWS_ECR_REGISTRY_ID=ECR_REGISTRY))
            self.assertEqual((result.returncode, calls), (0, ''))

    def test_docker_config_step_rejects_an_unusable_runner_temp(self):
        setup = self.step('docker_build', name='Use a job-scoped Docker config')['run']
        with tempfile.TemporaryDirectory() as runner_temp:
            for value in ('', runner_temp + '\nBASH_ENV=pwn.sh'):
                result, _, github_env, _ = self.run_script(setup, dict(RUNNER_TEMP=value))
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(github_env, '')


if __name__ == '__main__':
    unittest.main()
