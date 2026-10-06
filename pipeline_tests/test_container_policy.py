"""Trusted operator configuration cannot be weakened by task/model payloads."""
import pytest
from cochem_pipeline.container_policy import DockerPolicy, TestCommand as Command

IMAGE = 'sha256:' + 'a' * 64


def enabled(**values):
    return {'enabled': True, 'image': IMAGE, 'allowed_images': [IMAGE],
            'commands': [{'name': 'unit', 'argv': ['python', '-m', 'pytest', 'tests']}], **values}


def test_defaults_enforce_four_container_caps_and_immutable_round_trip():
    policy = DockerPolicy.from_dict(enabled())
    assert (policy.memory_mb, policy.cpus, policy.pids_limit, policy.tmpfs_mb, policy.max_containers) == (4096, 2, 512, 2048, 4)
    assert DockerPolicy.from_dict(policy.as_dict()) == policy
    assert len(policy.digest) == 64
    assert not DockerPolicy.from_dict().enabled


@pytest.mark.parametrize('values', [
    {'image': 'python:latest'}, {'allowed_images': []}, {'image': IMAGE.upper()},
    {'memory_mb': 8192}, {'cpus': 3}, {'pids_limit': 513}, {'tmpfs_mb': 4096},
    {'max_containers': 5}, {'cpus': float('nan')}, {'memory_mb': True},
    {'endpoint': 'tcp://127.0.0.1:2375'}, {'endpoint': 'ssh://remote'},
    {'commands': []}, {'enabled': 1}, {'unknown': True},
])
def test_policy_refuses_mutable_remote_unbounded_or_incomplete_configuration(values):
    with pytest.raises(ValueError):
        DockerPolicy.from_dict(enabled(**values))


def test_windows_local_named_pipe_is_explicit_and_does_not_need_wsl_paths():
    policy = DockerPolicy.from_dict(enabled(endpoint='npipe:////./pipe/dockerDesktopLinuxEngine', executable='C:\\Program Files\\Docker\\docker.exe'))
    assert policy.endpoint.startswith('npipe:')


@pytest.mark.parametrize('argv', [['pytest'], ['sh', '-c', 'pytest'],
                                ['python','-m','pytest','--junitxml=fake.xml'],
                                ['python','-m','pytest','--collect-only']])
def test_pytest_cannot_replace_owned_junit_or_omit_execution(argv):
    with pytest.raises(ValueError):
        Command.from_dict({'name': 'unit', 'argv': argv})


def test_custom_build_commands_are_argv_with_explicit_test_kind_and_budget():
    command = Command.from_dict({'name': 'build', 'kind': 'command', 'argv': ['npm','run','build'], 'timeout_seconds': 80})
    assert command.argv == ('npm','run','build')
    assert command.timeout_seconds == 80


def test_empty_pool_compatibility_ignores_unexecuted_command_variations():
    first=DockerPolicy.from_dict(enabled())
    second=DockerPolicy.from_dict(enabled(commands=[{'name':'build','kind':'command','argv':['python','-m','compileall','.']}]))
    assert first.digest!=second.digest
    assert first.pool_digest==second.pool_digest
    assert first.pool_digest!=DockerPolicy.from_dict(enabled(memory_mb=1024)).pool_digest


def test_lower_container_limit_defaults_to_matching_pool_size():
    policy=DockerPolicy.from_dict(enabled(max_containers=1))
    assert policy.warm_pool_size==1
