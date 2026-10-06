"""Small durable JSON writes owned by the supervisor, never by a model."""
import json
import os
from pathlib import Path
import uuid


def write_json(path: Path, data: dict) -> None:
    path=Path(path)
    temporary=path.with_name('.'+path.name+'.'+uuid.uuid4().hex)
    try:
        with temporary.open('x',encoding='utf-8') as stream:
            json.dump(data,stream,ensure_ascii=False,allow_nan=False,indent=2)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary,path)
    finally:
        temporary.unlink(missing_ok=True)
