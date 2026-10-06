"""Static generated-code gates; proposed programs are never imported or executed."""
from __future__ import annotations

import ast
import base64
import binascii
import re

_MODULES=('unittest.mock','mock','pytest_mock','mockito','flexmock','asynctest')
_NAMES={'MagicMock','Mock','AsyncMock','NonCallableMock','PropertyMock','monkeypatch',
        'MonkeyPatch','mocker','class_mocker','module_mocker','package_mocker','session_mocker'}
_TOKEN=re.compile(r'(?<![A-Za-z0-9_])(?:'+ '|'.join(map(re.escape,(*_MODULES,*_NAMES))) +r')(?![A-Za-z0-9_])')


def _fold(node):
    """Resolve bounded constant text, including common obfuscated import names."""
    if isinstance(node,ast.Constant) and isinstance(node.value,(str,bytes)):
        return node.value.decode('utf-8','replace') if isinstance(node.value,bytes) else node.value
    if isinstance(node,ast.BinOp) and isinstance(node.op,ast.Add):
        left,right=_fold(node.left),_fold(node.right)
        return left+right if left is not None and right is not None and len(left)+len(right)<=65536 else None
    if isinstance(node,ast.Call):
        name=getattr(node.func,'attr',getattr(node.func,'id',''))
        if name in {'decode','encode'} and isinstance(node.func,ast.Attribute):
            return _fold(node.func.value)
        if len(node.args)==1 and (value:=_fold(node.args[0])) is not None:
            try:
                if name in {'b64decode','standard_b64decode','urlsafe_b64decode'}:
                    return base64.b64decode(value,altchars=b'-_',validate=True).decode('utf-8')
                if name in {'fromhex','unhexlify'}:
                    return bytes.fromhex(value).decode('utf-8')
            except (ValueError,UnicodeError,binascii.Error):
                return None
    return None


def validate_generated_files(files, paths):
    """Reject mock-based evidence and unfinished generated Python routines.

    All imports/identifiers, fixture arguments, and constant dynamic-import
    expressions are checked. Encoded constant tokens are decoded without eval.
    This complements independent reviews; it is not a proof of arbitrary
    program semantics or permission to execute generated code on the host.
    """
    for path in paths:
        if not path.endswith('.py'):
            continue
        try:
            tree=ast.parse(files[path],filename=path)
        except (ValueError,SyntaxError,UnicodeError) as exc:
            raise ValueError('Generated Python must parse before execution: '+path) from exc
        unittest_names={'unittest'}
        for node in ast.walk(tree):
            if isinstance(node,ast.Import):
                unittest_names.update(item.asname or item.name for item in node.names if item.name=='unittest')
        for node in ast.walk(tree):
            forbidden=False
            if isinstance(node,(ast.Import,ast.ImportFrom)):
                for item in node.names:
                    name='.'.join(filter(None,(getattr(node,'module',None),item.name)))
                    forbidden |= any(name==module or name.startswith(module+'.') for module in _MODULES)
            if isinstance(node,(ast.Name,ast.Attribute,ast.arg)):
                forbidden |= getattr(node,'id',getattr(node,'attr',getattr(node,'arg',None))) in _NAMES
            if isinstance(node,ast.Attribute) and node.attr=='mock' and isinstance(node.value,ast.Name):
                forbidden |= node.value.id in unittest_names
            value=_fold(node)
            if value is not None and len(value)<=65536:
                forbidden |= bool(_TOKEN.search(value))
                for decode in (lambda text:base64.b64decode(text,validate=True),bytes.fromhex):
                    try:
                        forbidden |= bool(_TOKEN.search(decode(value).decode('utf-8')))
                    except (ValueError,UnicodeError,binascii.Error):
                        continue
            if forbidden:
                raise ValueError(f'Zero-mock gate rejected {path}:{node.lineno}')
            if isinstance(node,(ast.FunctionDef,ast.AsyncFunctionDef)):
                body=list(node.body)
                if body and isinstance(body[0],ast.Expr) and isinstance(getattr(body[0].value,'value',None),str):
                    body=body[1:]
                if len(body)==1 and (isinstance(body[0],ast.Pass) or
                        isinstance(body[0],ast.Expr) and isinstance(body[0].value,ast.Constant) and body[0].value.value is Ellipsis):
                    raise ValueError(f'Unfinished generated routine: {path}:{node.lineno}')
            if isinstance(node,ast.Raise):
                target=getattr(node.exc,'func',node.exc)
                if getattr(target,'id',getattr(target,'attr',None))=='NotImplementedError':
                    raise ValueError(f'Unfinished generated routine: {path}:{node.lineno}')
