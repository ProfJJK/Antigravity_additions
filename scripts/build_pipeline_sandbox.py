#!/usr/bin/env python3
"""Build the reviewed offline test image and emit its exact local image ID.

No source, credentials or private configuration enter the image. A supplied CA
is a BuildKit secret; it is never copied into a layer. Additional project test
dependencies belong in an operator-reviewed derivative Dockerfile.
"""
from __future__ import annotations
import argparse
import json
import os
from pathlib import Path
import subprocess
import tempfile


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--docker', default='docker')
    parser.add_argument('--endpoint', default='unix:///var/run/docker.sock' if os.name != 'nt'
                        else 'npipe:////./pipe/docker_engine')
    parser.add_argument('--base-image', default='python:3.12.11-slim-bookworm')
    parser.add_argument('--ca-bundle', type=Path)
    parser.add_argument('--tag', default='cochem-pipeline-tests:4.2.7')
    args = parser.parse_args()
    prefix = [args.docker, '--host', args.endpoint]
    env = dict(os.environ)
    for key in ('DOCKER_HOST','DOCKER_CONTEXT','DOCKER_TLS','DOCKER_TLS_VERIFY','DOCKER_CERT_PATH'):
        env.pop(key, None)
    dockerfile = Path(__file__).resolve().parents[1] / 'docker' / 'pipeline-tests.Dockerfile'
    # Empty context avoids accidentally sending repository secrets to the daemon.
    with tempfile.TemporaryDirectory(prefix='cochem-image-build-') as directory:
        context = Path(directory)
        (context / 'Dockerfile').write_bytes(dockerfile.read_bytes())
        command = [*prefix, 'build', '--pull', '--build-arg', 'BASE_IMAGE=' + args.base_image,
                   '--tag', args.tag]
        ca = args.ca_bundle
        if ca is None and os.environ.get('CODEX_PROXY_CERT'):
            ca = Path(os.environ['CODEX_PROXY_CERT'])
        if ca is not None:
            command += ['--secret', 'id=proxy_ca,src=' + str(ca.resolve(strict=True))]
        subprocess.run([*command, str(context)], env=env, check=True)
    result = subprocess.run([*prefix, 'image', 'inspect', '--format', '{{.Id}}', args.tag],
                            env=env, check=True, text=True, capture_output=True)
    image = result.stdout.strip()
    print(json.dumps({'image': image, 'allowed_images': [image], 'tag_for_humans': args.tag}))


if __name__ == '__main__':
    main()
