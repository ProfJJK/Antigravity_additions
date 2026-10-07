"""Export a validated public-corpus FTS5 snapshot; never touch a deployed index."""
from __future__ import annotations
import argparse
import hashlib
import json
from pathlib import Path
import sqlite3
import tempfile

from cochem_pipeline.knowledge import KnowledgeConfig, KnowledgeService


def export(corpus, output):
    corpus, output = Path(corpus).resolve(), Path(output).absolute()
    if output.exists() or output.is_symlink():
        raise ValueError('Snapshot output must be new; existing exports are immutable')
    with tempfile.TemporaryDirectory(prefix='cochem-knowledge-export-') as temporary:
        service = KnowledgeService(KnowledgeConfig(enabled=True,
            source_root=str(corpus/'.sources'), wiki_root=str(corpus/'wiki'),
            manifest_path=str(corpus/'v4.1.2_manifest.json'),
            state_root=str(Path(temporary)/'index')))
        try:
            evidence = service.refresh(full=True)
            if not evidence['index_size_sla_met']:
                raise ValueError('Index exceeds four-times corpus size')
            # Exclusive output creation prevents replacing a previously published database.
            with output.open('xb'):
                pass
            with service._reader() as (_, source):
                with sqlite3.connect(output) as destination:
                    source.backup(destination)
                    if destination.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
                        raise ValueError('Exported FTS5 snapshot failed integrity check')
            raw = output.read_bytes()
            return {**evidence, 'schema':'cochem-public-knowledge-export/1',
                'snapshot_file':output.name,'snapshot_sha256':hashlib.sha256(raw).hexdigest(),
                'snapshot_bytes':len(raw),'windows_deployment_updated':False,
                'scope':'Public ratified corpus only; protected deployed index is rebuilt locally'}
        finally:
            service.close()


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--corpus',required=True)
    parser.add_argument('--output',required=True)
    args=parser.parse_args()
    print(json.dumps(export(args.corpus,args.output),indent=2))


if __name__=='__main__':
    main()
