"""Fixed task entry point selecting only the protected, hash-verified release."""
import argparse
from pathlib import Path


BOOTSTRAP = (
    "import runpy,sys;sys.dont_write_bytecode=True;"
    "sys.path.insert(0,sys.argv[1]+'/src');"
    "sys.argv=['cochem-pipeline','daemon','--config',sys.argv[2]];"
    "runpy.run_module('cochem_pipeline',run_name='__main__')"
)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--pointer',required=True)
    parser.add_argument('--python',required=True)
    parser.add_argument('--config',required=True)
    args=parser.parse_args()
    from cochem_pipeline.windows import require_system,validate_private_path,validate_private_directory,validate_code_path
    from .cleanup import reconcile_stopped_containment
    from .windows import run_system_child
    from .releases import _check_record
    import json
    require_system()
    validate_private_path(args.pointer)
    validate_code_path(args.python)
    validate_code_path(args.config)
    pipeline_config=json.loads(Path(args.config).read_text(encoding='utf-8-sig'))
    private=Path(pipeline_config['private_root'])
    validate_private_directory(private)
    record=_check_record(json.loads(Path(args.pointer).read_text(encoding='utf-8')))
    source=Path(record['source_root'])
    validate_code_path(source/'src'/'cochem_pipeline'/'__main__.py')
    raise SystemExit(run_system_child([args.python,'-I','-c',BOOTSTRAP,str(source),args.config],source,
                                     control_file=Path(args.pointer).parent/'warden-process.json',
                                     on_tree_exit=lambda receipt: reconcile_stopped_containment(private/'job_board.db',receipt)))


if __name__=='__main__':
    main()
