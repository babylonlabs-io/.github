"""Exercise the scan gate of reusable_docker_pipeline.yml: a publishing run pushes
nothing unless every Trivy scan of that run passed (baby-auditor-infra-findings#432).

The shell blocks are extracted from the workflow at test time, so the tests run
the code that ships. Run: python3 -m unittest discover -s tests -v
(requires bash and yq v4).
"""
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

WORKFLOW = Path(__file__).resolve().parents[1] / '.github/workflows/reusable_docker_pipeline.yml'

GATE = 'Require passing scans before publishing'
POLICY = 'Require image scanning for publication'
SCAN_STEPS = ('fs-scan', 'fs-secret-scan', 'image-scan', 'image-secret-scan')
PUSH_STEPS = ('Login to Docker Hub', 'Configure AWS credentials', 'Login to Amazon ECR',
              'Push to Docker Hub', 'Push to ECR')
OUTCOMES = {
    'FS_SCAN_OUTCOME': '${{ steps.fs-scan.outcome }}',
    'FS_SECRET_OUTCOME': '${{ steps.fs-secret-scan.outcome }}',
    'IMAGE_SCAN_OUTCOME': '${{ steps.image-scan.outcome }}',
    'IMAGE_SECRET_OUTCOME': '${{ steps.image-secret-scan.outcome }}',
}


def load_workflow():
    return json.loads(subprocess.check_output(['yq', '-o=json', str(WORKFLOW)]))


class DockerPipelineScanGate(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        workflow = load_workflow()
        cls.jobs = workflow['jobs']
        cls.inputs = next(value for key, value in workflow.items() if key in ('on', True, 'true'))['workflow_call']['inputs']
        cls.bash = shutil.which('bash')
        if not cls.bash:
            raise RuntimeError('bash is required')

    def step(self, job, step_id=None, name=None):
        for step in self.jobs[job]['steps']:
            if (step_id and step.get('id') == step_id) or (name and step.get('name') == name):
                return step
        raise AssertionError(f'step not found: {job} {step_id or name}')

    def run_script(self, script, env):
        with tempfile.TemporaryDirectory() as directory:
            bin_dir = Path(directory) / 'bin'
            bin_dir.mkdir()
            for tool in ('echo',):
                source = shutil.which(tool)
                if source:
                    (bin_dir / tool).symlink_to(source)
            full_env = dict(PATH=str(bin_dir))
            full_env.update(env)
            return subprocess.run([self.bash, '--noprofile', '--norc', '-e', '-c', script],
                                  env=full_env, text=True, capture_output=True)

    def test_gate_sits_between_the_scans_and_every_registry_step(self):
        names = [step.get('name') for step in self.jobs['docker_build']['steps']]
        ids = [step.get('id') for step in self.jobs['docker_build']['steps']]
        gate = names.index(GATE)
        for scan in SCAN_STEPS:
            self.assertLess(ids.index(scan), gate, scan)
        for push in PUSH_STEPS:
            self.assertLess(gate, names.index(push), push)
        self.assertLess(names.index('Build image for scanning'), gate)

    def test_gate_is_a_hard_failure_bound_to_this_runs_scan_outcomes(self):
        gate = self.step('docker_build', name=GATE)
        self.assertNotIn('continue-on-error', gate)
        self.assertEqual(gate['if'], 'inputs.publish && (inputs.push_to_dockerhub || inputs.push_to_ecr)')
        self.assertEqual(gate['env'], dict(OUTCOMES, DOCKER_SCAN='${{ inputs.docker_scan }}',
                                           PLATFORM='${{ matrix.platform }}'))
        self.assertNotIn('nofail', json.dumps(gate['env']))
        self.assertNotIn('${{', gate['run'])

    def test_every_registry_step_is_gated_on_publish_and_never_always(self):
        for name in PUSH_STEPS:
            step = self.step('docker_build', name=name)
            with self.subTest(step=name):
                self.assertTrue(step['if'].startswith('inputs.publish && '), step['if'])
                self.assertNotIn('always()', step['if'])
                self.assertNotIn('continue-on-error', step)

    def test_enforcement_scans_fail_on_findings(self):
        for scan in SCAN_STEPS:
            step = self.step('docker_build', step_id=scan)
            with self.subTest(scan=scan):
                self.assertTrue(step['uses'].startswith('aquasecurity/trivy-action@'))
                self.assertEqual(step['with']['exit-code'], '1')

    def test_policy_is_checked_before_any_credential_exists(self):
        validator = self.jobs['prepare-metadata']
        self.assertEqual(validator['permissions'], {})
        policy = self.step('prepare-metadata', name=POLICY)
        self.assertNotIn('if', policy)
        self.assertNotIn('continue-on-error', policy)
        self.assertEqual(set(policy['env']), {'PUBLISH', 'PUSH_TO_DOCKERHUB', 'PUSH_TO_ECR', 'DOCKER_SCAN'})
        self.assertEqual(self.inputs['docker_scan']['default'], True)
        self.assertEqual(self.inputs['trivy_nofail']['default'], False)
        self.assertEqual(self.inputs['secret_nofail']['default'], False)

    def gate(self, platform='linux/amd64', docker_scan='true', **outcomes):
        env = dict(DOCKER_SCAN=docker_scan, PLATFORM=platform,
                   FS_SCAN_OUTCOME='success', FS_SECRET_OUTCOME='success',
                   IMAGE_SCAN_OUTCOME='success', IMAGE_SECRET_OUTCOME='success')
        env.update(outcomes)
        return self.run_script(self.step('docker_build', name=GATE)['run'], env)

    def test_gate_passes_only_when_every_scan_of_the_leg_succeeded(self):
        self.assertEqual(self.gate().returncode, 0)
        self.assertEqual(self.gate(platform='linux/arm64', FS_SCAN_OUTCOME='skipped',
                                   FS_SECRET_OUTCOME='skipped').returncode, 0)

    def test_gate_fails_on_any_failed_or_missing_scan(self):
        for outcome in ('failure', 'skipped', 'cancelled', ''):
            for variable in OUTCOMES:
                with self.subTest(variable=variable, outcome=outcome):
                    result = self.gate(**{variable: outcome})
                    self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
                    self.assertIn('::error::', result.stdout)
        for variable in ('IMAGE_SCAN_OUTCOME', 'IMAGE_SECRET_OUTCOME'):
            with self.subTest(platform='linux/arm64', variable=variable):
                result = self.gate(platform='linux/arm64', FS_SCAN_OUTCOME='skipped',
                                   FS_SECRET_OUTCOME='skipped', **{variable: 'failure'})
                self.assertEqual(result.returncode, 1, result.stdout + result.stderr)

    def test_gate_refuses_an_unscanned_image(self):
        for docker_scan in ('false', '', 'True', 'yes'):
            with self.subTest(docker_scan=docker_scan):
                result = self.gate(docker_scan=docker_scan)
                self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
                self.assertIn('docker_scan', result.stdout)

    def test_gate_ignores_the_nofail_inputs(self):
        for variable in ('TRIVY_NOFAIL', 'SECRET_NOFAIL'):
            result = self.gate(IMAGE_SCAN_OUTCOME='failure', IMAGE_SECRET_OUTCOME='failure', **{variable: 'true'})
            self.assertEqual(result.returncode, 1, result.stdout + result.stderr)

    def policy(self, publish, docker_scan, dockerhub='false', ecr='true'):
        return self.run_script(self.step('prepare-metadata', name=POLICY)['run'],
                               dict(PUBLISH=publish, DOCKER_SCAN=docker_scan,
                                    PUSH_TO_DOCKERHUB=dockerhub, PUSH_TO_ECR=ecr))

    def test_publishing_without_scans_fails_before_any_job_holds_credentials(self):
        for dockerhub, ecr in (('false', 'true'), ('true', 'false'), ('true', 'true')):
            with self.subTest(dockerhub=dockerhub, ecr=ecr):
                result = self.policy('true', 'false', dockerhub, ecr)
                self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
                self.assertIn('::error::publish requires docker_scan: true', result.stdout)
        self.assertEqual(self.policy('true', 'true').returncode, 0)
        self.assertEqual(self.policy('false', 'false').returncode, 0)
        self.assertEqual(self.policy('true', 'false', 'false', 'false').returncode, 0)


if __name__ == '__main__':
    unittest.main()
