"""Unprivileged cochem-knowledge-mcp; reuse the authenticated controller boundary."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from .server import create_knowledge_server
from .service import ControlClient


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--client-config',required=True)
    args=parser.parse_args()
    config=json.loads(Path(args.client_config).read_text(encoding='utf-8-sig'))
    client=ControlClient(config.get('port',47824),config['token_file'])
    create_knowledge_server(client).run(transport='stdio')


if __name__=='__main__':
    main()
