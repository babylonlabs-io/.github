"""Exercise the actual ECR workflow scripts without AWS credentials or Docker.

Run: python3 -m unittest discover -s tests -v (requires yq v4 and jq).
"""
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest


class ECRTagCollisions(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        workflow = Path(__file__).resolve().parents[1] / '.github/workflows/reusable_docker_pipeline.yml'
        cls.jobs = json.loads(subprocess.check_output(['yq', '-o=json', str(workflow)]))['jobs']
        cls.bash = shutil.which('bash')
        cls.safe_commands = {name: shutil.which(name) for name in ('echo', 'jq', 'xargs', 'tr')}
        if not cls.bash or not all(cls.safe_commands.values()):
            raise RuntimeError('bash, echo, jq, xargs, and tr are required')

    def run_step(self, job, step_name, response, aws_exit=0, docker_exit=0):
        step = next(step for step in self.jobs[job]['steps'] if step.get('name') == step_name)
        with tempfile.TemporaryDirectory() as directory:
            directory = Path(directory)
            for name, script in {
                'aws': '#!/bin/bash\nprintf "%s\\n" "$*" >> "$AWS_LOG"\nprintf "%s\\n" "$AWS_RESPONSE"\nexit "$AWS_EXIT"\n',
                'docker': '#!/bin/bash\nprintf "%s\\n" "$*" >> "$DOCKER_LOG"\nif [ "$1" = tag ]; then exit 0; fi\nexit "$DOCKER_EXIT"\n',
            }.items():
                command = directory / name
                command.write_text(script)
                command.chmod(0o755)
            for name, source in self.safe_commands.items():
                (directory / name).symlink_to(source)
            # Deliberately do not inherit credentials, proxy settings, or the
            # host PATH. A future workflow regression cannot find curl, a real
            # aws CLI, or a real Docker daemon from this fixture.
            env = dict(PATH=str(directory),
                       AWS_LOG=str(directory / 'aws.log'), DOCKER_LOG=str(directory / 'docker.log'),
                       AWS_RESPONSE=response, AWS_EXIT=str(aws_exit), DOCKER_EXIT=str(docker_exit),
                       AWS_ECR_REGISTRY_ID='123456789012.dkr.ecr.ap-east-1.amazonaws.com',
                       IMAGE_NAME='victim', IMAGE_TAG='release', PLATFORM_PAIR='linux-amd64',
                       BUILD_PREFIX='', GIT_SHA='a' * 40,
                       BUILD_MATRIX=json.dumps({'include': [{'platform': 'linux/amd64'}, {'platform': 'linux/arm64'}]}),
                       DOCKER_METADATA_OUTPUT_JSON=json.dumps({'tags': ['123456789012.dkr.ecr.ap-east-1.amazonaws.com/victim:release']}))
            result = subprocess.run([self.bash, '-e', '-o', 'pipefail', '-c', step['run']],
                                    env=env, text=True, capture_output=True)
            docker_log = directory / 'docker.log'
            calls = docker_log.read_text() if docker_log.exists() else ''
            aws_calls = (directory / 'aws.log').read_text()
            self.assertIn('ecr batch-get-image ', aws_calls)
            self.assertNotIn('describe-images', aws_calls)
            return result, calls

    def test_platform_and_manifest_publication(self):
        for job, step_name, tag, expected_push in [
            ('docker_build', 'Push to ECR', 'release-linux-amd64', 'push '),
            ('merge_ecr', 'Create manifest list and push', 'release', 'buildx imagetools create '),
        ]:
            missing = {'images': [], 'failures': [{'failureCode': 'ImageNotFound', 'imageId': {'imageTag': tag}}]}
            cases = [
                ('absent', missing, 0, 0, True),
                ('existing', {'images': [{'imageId': {'imageTag': tag}}], 'failures': []}, 0, 0, False),
                ('access-denied', 'AccessDeniedException', 254, 0, False),
                ('service-error', {'images': [], 'failures': [{'failureCode': 'UpstreamUnavailable'}]}, 0, 0, False),
                ('wrong-tag', {'images': [], 'failures': [{'failureCode': 'ImageNotFound', 'imageId': {'imageTag': 'other'}}]}, 0, 0, False),
                ('empty-response', {'images': [], 'failures': []}, 0, 0, False),
                ('malformed-json', 'invalid JSON', 0, 0, False),
                ('missing-fields', {}, 0, 0, False),
                ('immutable-race', missing, 0, 1, False),
            ]
            for name, response, aws_exit, docker_exit, success in cases:
                with self.subTest(job=job, scenario=name):
                    result, calls = self.run_step(job, step_name,
                                                  response if isinstance(response, str) else json.dumps(response),
                                                  aws_exit, docker_exit)
                    self.assertEqual(result.returncode == 0, success, result.stderr + result.stdout)
                    if success:
                        self.assertIn(expected_push, calls)
                    elif name == 'immutable-race':
                        self.assertIn(expected_push, calls)
                    else:
                        self.assertEqual(calls, '', 'Rejected lookups must never reach Docker')
                    if name == 'existing':
                        self.assertIn('already exists', result.stdout)
                        self.assertIn('fresh tag', result.stdout)


if __name__ == '__main__':
    unittest.main()
