"""Registered dual-wiki retrieval with private, replaceable FTS5 generations.

Only the controller indexes or opens corpus files. Callers name catalog entries,
never filesystem paths. A ratified manifest covers the complete Markdown corpus;
accepted .sources bytes are append-only. Full and incremental refreshes publish a
complete generation atomically, so readers never observe half an index. Source
files and the manifest are never modified by this service.
"""
from __future__ import annotations

from contextlib import contextmanager, closing
from dataclasses import asdict, dataclass
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import queue
import shutil
import sqlite3
import stat
import threading
import time
from urllib.parse import unquote, urlsplit
import uuid


class KnowledgeError(ValueError):
    """Corpus, catalog or index evidence does not satisfy the registered policy."""


class KnowledgeFileChanged(KnowledgeError):
    """An ordinary file changed across one verified read; no bytes were accepted."""


@dataclass(frozen=True)
class KnowledgeConfig:
    enabled: bool = False
    source_root: str = ''
    wiki_root: str = ''
    state_root: str = ''
    manifest_path: str = ''
    max_document_bytes: int = 1048576
    max_corpus_bytes: int = 134217728
    max_documents: int = 20000

    def __post_init__(self):
        if type(self.enabled) is not bool:
            raise KnowledgeError('knowledge.enabled must be a boolean')
        for name, maximum in (('max_document_bytes',1048576),('max_corpus_bytes',134217728),('max_documents',20000)):
            value = getattr(self,name)
            if type(value) is not int or not 1 <= value <= maximum:
                raise KnowledgeError(name+' exceeds the bounded knowledge policy')
        for name in ('source_root','wiki_root','state_root','manifest_path'):
            value = getattr(self,name)
            if not isinstance(value,str) or '\x00' in value:
                raise KnowledgeError(name+' must be a path string')
            if self.enabled and (not Path(value).is_absolute() or '..' in Path(value).parts):
                raise KnowledgeError('Enabled knowledge paths must be absolute without traversal')
        if self.enabled:
            source,wiki,manifest,state = (Path(getattr(self,name)) for name in
                ('source_root','wiki_root','manifest_path','state_root'))
            if (source.name != '.sources' or wiki.name != 'wiki' or source.parent != wiki.parent
                    or manifest != source.parent/'v4.1.2_manifest.json'):
                raise KnowledgeError('Register sibling .sources/wiki and their v4.1.2_manifest.json')
            if state == source.parent or source.parent in state.parents or state in source.parent.parents:
                raise KnowledgeError('Knowledge state must be disjoint from the corpus')

    @classmethod
    def from_dict(cls,value=None):
        if value is None:
            return cls()
        if not isinstance(value,dict) or set(value)-set(cls.__dataclass_fields__):
            raise KnowledgeError('Unknown knowledge configuration fields')
        return cls(**value)

    def as_dict(self):
        return asdict(self)

    def validate_placement(self,private_root,forbidden=()):
        if not self.enabled:
            return
        state,private,corpus = Path(self.state_root).resolve(),Path(private_root).resolve(),Path(self.source_root).parent.resolve()
        if private not in state.parents:
            raise KnowledgeError('Knowledge state must be a dedicated child of protected private_root')
        for path in (private,*(Path(item).resolve() for item in forbidden)):
            if corpus == path or corpus in path.parents or path in corpus.parents:
                raise KnowledgeError('Knowledge corpus must be disjoint from credentials, private state and worker/project paths')


def _digest(raw):
    return hashlib.sha256(raw).hexdigest()


def _ordinary(path,*,directory=False):
    info = path.lstat()
    if (stat.S_ISLNK(info.st_mode) or getattr(info,'st_file_attributes',0)&0x400
            or not (stat.S_ISDIR(info.st_mode) if directory else stat.S_ISREG(info.st_mode))
            or (not directory and info.st_nlink != 1)):
        raise KnowledgeError('Knowledge paths must be ordinary files/directories without links: '+str(path))
    return info


def _protected(path,*,directory=False,private=False):
    info = _ordinary(path,directory=directory)
    if os.name == 'nt':
        from .windows import validate_private_path
        validate_private_path(path)
    elif info.st_uid != os.getuid() or info.st_mode & (0o077 if private else 0o022):
        raise KnowledgeError('Knowledge paths require controller ownership and protected permissions: '+str(path))
    return info


def _ancestors(path):
    for parent in (path,*path.parents):
        _ordinary(parent,directory=True)


def _read_bytes(path,limit,*,private=False):
    before = _protected(path,private=private)
    if before.st_size > limit:
        raise KnowledgeError('Knowledge file exceeds its configured byte bound')
    flags = os.O_RDONLY | getattr(os,'O_BINARY',0) | getattr(os,'O_NOFOLLOW',0)
    descriptor = os.open(path,flags)
    with os.fdopen(descriptor,'rb') as stream:
        opened = os.fstat(stream.fileno())
        if (opened.st_dev,opened.st_ino) != (before.st_dev,before.st_ino):
            raise KnowledgeFileChanged('Knowledge file changed while opening')
        raw = stream.read(limit+1)
    after = _ordinary(path)
    if (len(raw)>limit or (before.st_dev,before.st_ino,before.st_size,before.st_mtime_ns) !=
            (after.st_dev,after.st_ino,after.st_size,after.st_mtime_ns)):
        raise KnowledgeFileChanged('Knowledge file changed while reading')
    return raw


def _key(value):
    if (not isinstance(value,str) or not value or len(value)>1024 or '\\' in value or ':' in value
            or any(ord(char)<32 for char in value)):
        raise KnowledgeError('A knowledge document needs a registered relative catalog path')
    parts = value.split('/')
    if len(parts)<2 or parts[0] not in ('.sources','wiki') or any(part in ('','.','..') for part in parts):
        raise KnowledgeError('Knowledge paths cannot escape the registered collections')
    for part in parts:
        if part.endswith((' ','.')) or part.split('.')[0].upper() in {'CON','PRN','AUX','NUL',*(f'{p}{n}' for p in ('COM','LPT') for n in range(1,10))}:
            raise KnowledgeError('Windows-ambiguous knowledge path')
    if PurePosixPath(value).suffix.lower() != '.md':
        raise KnowledgeError('Only registered Markdown documents are retrievable')
    return value


def _sections(text,title):
    current,lines,fence = title,[],None
    for line in text.splitlines(keepends=True):
        marker = re.match(r'^\s{0,3}(`{3,}|~{3,})',line)
        if marker:
            token = marker.group(1)
            if fence is None:
                fence = (token[0],len(token))
            elif token[0]==fence[0] and len(token)>=fence[1]:
                fence = None
        heading = re.match(r'^#{1,6}\s+(.+?)\s*#*\s*$',line) if fence is None else None
        if heading:
            if lines and ''.join(lines).strip():
                yield current,''.join(lines)
            current,lines = heading.group(1),[line]
        else:
            lines.append(line)
    if lines and ''.join(lines).strip():
        yield current,''.join(lines)


def _links(text):
    """Read inline/reference Markdown links, excluding code examples."""
    lines,fence = [],None
    for line in text.splitlines():
        marker = re.match(r'^\s{0,3}(`{3,}|~{3,})',line)
        if marker:
            token = marker.group(1)
            if fence is None:
                fence=(token[0],len(token))
            elif token[0]==fence[0] and len(token)>=fence[1]:
                fence=None
            continue
        if fence is None:
            lines.append(re.sub(r'(`+).*?\1','',line))
    content='\n'.join(lines)
    definitions={}
    for match in re.finditer(r'^\s{0,3}\[([^]\n]+)\]:\s*(?:<([^>]+)>|(\S+))',content,re.M):
        definitions[match.group(1).casefold()]=match.group(2) or match.group(3)
    yield from definitions.values()
    for match in re.finditer(r'!?\[[^]\n]*\]\(\s*(?:<([^>]+)>|([^\s)]+))(?:\s+["\'][^\n]*?["\'])?\s*\)',content):
        yield match.group(1) or match.group(2)
    for match in re.finditer(r'!?\[([^]\n]+)\]\[([^]\n]*)\]',content):
        label=(match.group(2) or match.group(1)).casefold()
        if label not in definitions:
            raise KnowledgeError('Undefined Markdown reference link: '+label)
    for match in re.finditer(r'<([^<>\s]+)>',content):
        if '.md' in match.group(1):
            yield match.group(1)


def _anchors(text):
    counts,result = {},set()
    for title,_ in _sections(text,''):
        slug=re.sub(r'[^\w\- ]','',title.casefold()).replace(' ','-')
        ordinal=counts.get(slug,0);counts[slug]=ordinal+1
        result.add(slug if not ordinal else slug+'-'+str(ordinal))
    return result


def _write_json(path,data):
    temporary=path.with_name('.'+path.name+'.'+uuid.uuid4().hex)
    descriptor=os.open(temporary,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
    try:
        with os.fdopen(descriptor,'w',encoding='utf-8') as stream:
            json.dump(data,stream,ensure_ascii=False,sort_keys=True)
            stream.flush();os.fsync(stream.fileno())
        _protected(temporary,private=True)
        os.replace(temporary,path)
    finally:
        temporary.unlink(missing_ok=True)


class KnowledgeService:
    def __init__(self,config:KnowledgeConfig):
        if not isinstance(config,KnowledgeConfig) or not config.enabled:
            raise KnowledgeError('Knowledge service requires an enabled registered configuration')
        self.config=config
        self.root=Path(config.source_root).parent
        self.state=Path(config.state_root)
        _ancestors(self.root)
        _protected(self.root,directory=True)
        _ancestors(self.state.parent)
        _protected(self.state.parent,directory=True,private=True)
        self.state.mkdir(mode=0o700,exist_ok=True)
        _protected(self.state,directory=True,private=True)
        self._writer=threading.Lock()
        self._background_lock=threading.Lock()
        self._background=None
        self._closed=False
        self._last_error=None
        self._readers=queue.LifoQueue(maxsize=2)

    @contextmanager
    def _write_lock(self):
        with self._writer:
            lock=self.state/'writer.lock'
            if lock.exists():
                _protected(lock,private=True)
            fd=os.open(lock,os.O_RDWR|os.O_CREAT|getattr(os,'O_NOFOLLOW',0),0o600)
            with os.fdopen(fd,'r+b') as stream:
                if os.fstat(stream.fileno()).st_size==0:
                    stream.write(b'0');stream.flush()
                _protected(lock,private=True)
                if os.name=='nt':
                    import msvcrt
                    stream.seek(0);msvcrt.locking(stream.fileno(),msvcrt.LK_NBLCK,1)
                else:
                    import fcntl
                    fcntl.flock(stream.fileno(),fcntl.LOCK_EX|fcntl.LOCK_NB)
                try:
                    yield
                finally:
                    if os.name=='nt':
                        stream.seek(0);msvcrt.locking(stream.fileno(),msvcrt.LK_UNLCK,1)
                    else:
                        fcntl.flock(stream.fileno(),fcntl.LOCK_UN)

    def _path(self,key):
        key=_key(key)
        path=self.root/Path(key)
        for parent in (path.parent,*path.parent.parents):
            _protected(parent,directory=True)
            if parent==self.root:
                break
        return path

    def _document(self,key):
        raw=_read_bytes(self._path(key),self.config.max_document_bytes)
        text=raw.decode('utf-8',errors='strict')
        lines=len(text.splitlines())
        if key.startswith('wiki/') and lines>400:
            raise KnowledgeError('Wiki/SRS chapters must not exceed 400 lines: '+key)
        if not text.strip() or '\x00' in text:
            raise KnowledgeError('Knowledge documents must be nonempty UTF-8 text')
        sections=list(_sections(text,Path(key).stem))
        return {'path':key,'sha256':_digest(raw),'size_bytes':len(raw),'line_count':lines,
                'title':sections[0][0],'sections':len(sections)},text,sections

    def _catalog(self):
        raw=_read_bytes(Path(self.config.manifest_path),8*1048576)
        manifest=json.loads(raw.decode('utf-8'))
        if not isinstance(manifest,dict) or not isinstance(manifest.get('documents'),list):
            raise KnowledgeError('v4.1.2_manifest.json requires a complete documents catalog')
        catalog={};seen=set()
        for item in manifest['documents']:
            if not isinstance(item,dict) or set(item)-{'path','sha256'}:
                raise KnowledgeError('Manifest documents require exact path and sha256 entries')
            key=_key(item.get('path'));digest=item.get('sha256')
            if (key.casefold() in seen or not isinstance(digest,str)
                    or not re.fullmatch('[0-9a-f]{64}',digest)):
                raise KnowledgeError('Manifest contains a duplicate path or invalid SHA-256')
            catalog[key]=digest
            seen.add(key.casefold())
        if not 1<=len(catalog)<=self.config.max_documents:
            raise KnowledgeError('Manifest document count exceeds the configured bound')
        if 'srs_documents' in manifest:
            srs=manifest['srs_documents']
            if (not isinstance(srs,list) or any(not isinstance(key,str) or key not in catalog for key in srs)
                    or len(set(srs))!=len(srs)):
                raise KnowledgeError('Manifest srs_documents must identify distinct catalog entries')
        return catalog,_digest(raw),set(manifest.get('srs_documents',[]))

    def _inventory(self):
        result={}
        for root in (Path(self.config.source_root),Path(self.config.wiki_root)):
            _protected(root,directory=True)
            for current,dirs,files in os.walk(root,followlinks=False):
                for name in dirs:
                    _protected(Path(current)/name,directory=True)
                for name in files:
                    path=Path(current)/name
                    _protected(path)
                    if path.suffix.lower()=='.md':
                        key=_key(path.relative_to(self.root).as_posix())
                        if key.casefold() in result:
                            raise KnowledgeError('Case-insensitive corpus path collision')
                        result[key.casefold()]=key
                if len(result)>self.config.max_documents:
                    raise KnowledgeError('Corpus exceeds configured document count')
        return set(result.values())

    def _pins(self):
        path=self.state/'sources.json'
        if not path.exists():
            return {}
        data=json.loads(_read_bytes(path,8*1048576,private=True).decode('utf-8'))
        if (not isinstance(data,dict) or any(not isinstance(key,str) or not key.startswith('.sources/')
                or not isinstance(value,str) or not re.fullmatch('[0-9a-f]{64}',value) for key,value in data.items())):
            raise KnowledgeError('Immutable source archive ledger is invalid')
        return data

    def validate(self):
        catalog,manifest_hash,srs=self._catalog()
        if self._inventory()!=set(catalog):
            raise KnowledgeError('Manifest catalog and physical Markdown corpus are not synchronized')
        records={};total=0;links=0;chapter_links={}
        for key,expected in catalog.items():
            record,text,_=self._document(key)
            if record['sha256']!=expected:
                raise KnowledgeError('Manifest hash does not match physical document: '+key)
            if key in srs and record['line_count']>400:
                raise KnowledgeError('Manifest SRS chapters must not exceed 400 lines')
            records[key]=record;total+=record['size_bytes']
            if total>self.config.max_corpus_bytes:
                raise KnowledgeError('Knowledge corpus exceeds its byte bound')
            if not key.startswith('wiki/'):
                continue
            for link in _links(text):
                parsed=urlsplit(link)
                if parsed.scheme in ('http','https','mailto'):
                    continue
                if parsed.scheme or parsed.netloc or '\\' in link:
                    raise KnowledgeError('Wiki link uses an unregistered scheme or path')
                relative=unquote(parsed.path)
                if relative.startswith('/') or '\\' in relative or '\x00' in relative:
                    raise KnowledgeError('Wiki links cannot name absolute files')
                target=(self.root/Path(key)).parent/relative if relative else self.root/Path(key)
                # Normalize lexical '..' only after checking the registered boundary.
                target=Path(os.path.abspath(target))
                if not any(parent==target.parent or parent in target.parents for parent in
                           (Path(self.config.source_root),Path(self.config.wiki_root))):
                    raise KnowledgeError('Wiki link escapes the registered corpus')
                for parent in (target.parent,*target.parent.parents):
                    _protected(parent,directory=True)
                    if parent==self.root:
                        break
                _protected(target)
                if target.suffix.lower()=='.md':
                    target_key=target.relative_to(self.root).as_posix()
                    chapter_links.setdefault(key,set()).add(target_key)
                    if target_key not in catalog:
                        raise KnowledgeError('Wiki link target is absent from the catalog')
                    if parsed.fragment:
                        _,target_text,_=self._document(target_key)
                        if unquote(parsed.fragment) not in _anchors(target_text):
                            raise KnowledgeError('Wiki link has a broken heading reference: '+link)
                links+=1
        if srs:
            skeletons={key for key in srs if key.endswith('/00_skeleton.md')}
            if not skeletons or not srs-skeletons <= set().union(*(chapter_links.get(key,set()) for key in skeletons)):
                raise KnowledgeError('SRS skeleton must link every registered SRS chapter')
        for key,digest in self._pins().items():
            if catalog.get(key)!=digest:
                raise KnowledgeError('Previously accepted .sources documents are immutable: '+key)
        return {'valid':True,'manifest_sha256':manifest_hash,'documents':records,
                'document_count':len(records),'section_count':sum(row['sections'] for row in records.values()),
                'corpus_bytes':total,'relative_links_checked':links}

    def _current(self):
        for attempt in range(3):
            try:
                record=json.loads(_read_bytes(self.state/'current.json',16384,private=True).decode('utf-8'))
                break
            except KnowledgeFileChanged:
                if attempt==2:
                    raise
        if not isinstance(record,dict) or not re.fullmatch('g-[0-9a-f]{32}',str(record.get('generation',''))):
            raise KnowledgeError('Knowledge generation pointer is invalid')
        path=self.state/record['generation']/'knowledge_index.db'
        _protected(path.parent,directory=True,private=True)
        _protected(path,private=True)
        with path.open('rb') as stream:
            if stream.read(16)!=b'SQLite format 3\x00':
                raise sqlite3.DatabaseError('Knowledge index binary header is corrupt')
        return record,path

    @contextmanager
    def _reader(self):
        if self._closed:
            raise KnowledgeError('Knowledge service is closed')
        record,path=self._current()
        conn=None
        while conn is None:
            try:
                generation,candidate=self._readers.get_nowait()
            except queue.Empty:
                break
            if generation==record['generation']:
                conn=candidate
            else:
                candidate.close()
        if conn is None:
            # Published generations never mutate. Reuse bounded, independently
            # owned immutable read connections; every request still verifies the
            # protected pointer and executes its real FTS query (no result cache).
            conn=sqlite3.connect(path.as_uri()+'?mode=ro&immutable=1',uri=True,timeout=.5,check_same_thread=False)
            conn.row_factory=sqlite3.Row
            conn.execute('PRAGMA query_only=ON');conn.execute('PRAGMA cache_size=-8192')
        try:
            yield record,conn
        finally:
            if self._closed:
                conn.close()
            else:
                try:
                    self._readers.put_nowait((record['generation'],conn))
                except queue.Full:
                    conn.close()

    def _close_readers(self):
        while True:
            try:
                _,reader=self._readers.get_nowait()
            except queue.Empty:
                return
            reader.close()

    def refresh(self,full=False):
        if type(full) is not bool or self._closed:
            raise KnowledgeError('Refresh requires a live service and a boolean full flag')
        with self._write_lock():
            validated=self.validate()
            previous=None;existing={};corruption=False
            try:
                with self._reader() as (previous,reader):
                    if reader.execute('PRAGMA quick_check').fetchone()[0]!='ok':
                        raise sqlite3.DatabaseError('Knowledge index failed its integrity check')
                    existing={row['doc_path']:row['sha256'] for row in reader.execute('SELECT doc_path,sha256 FROM documents')}
            except FileNotFoundError:
                if (self.state/'current.json').exists():
                    corruption=True
            except sqlite3.DatabaseError:
                corruption=True
            if previous and not full and not corruption and previous['manifest_sha256']==validated['manifest_sha256']:
                self._last_error=None
                return {**previous,'changed_documents':0,'removed_documents':0,'rebuilt_corruption':False}
            generation='g-'+uuid.uuid4().hex
            folder=self.state/generation;folder.mkdir(mode=0o700)
            target=folder/'knowledge_index.db'
            changed=0
            try:
                fd=os.open(target,os.O_CREAT|os.O_EXCL|os.O_WRONLY,0o600);os.close(fd)
                with closing(sqlite3.connect(target)) as writer:
                    writer.execute('PRAGMA cache_size=-4096')
                    writer.execute('PRAGMA journal_mode=DELETE')
                    if previous and not full and not corruption:
                        with self._reader() as (_,reader):
                            reader.backup(writer)
                    else:
                        writer.executescript('''CREATE TABLE documents (
                            id INTEGER PRIMARY KEY AUTOINCREMENT,doc_path TEXT UNIQUE NOT NULL,
                            title TEXT NOT NULL,line_count INTEGER NOT NULL,sha256 TEXT NOT NULL,
                            updated_at INTEGER DEFAULT (strftime('%s','now')));
                            CREATE VIRTUAL TABLE fts_index USING fts5(doc_path UNINDEXED,
                            section_title,content,tokenize='porter unicode61');''')
                        existing={}
                    removed=set(existing)-set(validated['documents'])
                    with writer:
                        for key in removed:
                            writer.execute('DELETE FROM fts_index WHERE doc_path=?',(key,))
                            writer.execute('DELETE FROM documents WHERE doc_path=?',(key,))
                        for key,record in validated['documents'].items():
                            if existing.get(key)==record['sha256']:
                                continue
                            actual,_,sections=self._document(key)
                            if actual!=record:
                                raise KnowledgeError('Corpus changed during index construction')
                            writer.execute('DELETE FROM fts_index WHERE doc_path=?',(key,))
                            writer.execute('''INSERT INTO documents(doc_path,title,line_count,sha256) VALUES (?,?,?,?)
                                ON CONFLICT(doc_path) DO UPDATE SET title=excluded.title,line_count=excluded.line_count,
                                sha256=excluded.sha256,updated_at=strftime('%s','now')''',
                                (key,record['title'],record['line_count'],record['sha256']))
                            writer.executemany('INSERT INTO fts_index(doc_path,section_title,content) VALUES (?,?,?)',
                                               ((key,title,content) for title,content in sections))
                            changed+=1
                        writer.execute("INSERT INTO fts_index(fts_index) VALUES ('optimize')")
                        writer.execute("INSERT INTO fts_index(fts_index) VALUES ('integrity-check')")
                    writer.execute('VACUUM')
                    if writer.execute('PRAGMA integrity_check').fetchone()[0]!='ok':
                        raise KnowledgeError('New knowledge generation failed integrity verification')
                # No filesystem IO occurs inside a published index transaction.
                # Revalidate after construction so concurrent operator edits cannot
                # publish a catalog claiming different source bytes.
                revalidated=self.validate()
                if revalidated!=validated:
                    raise KnowledgeError('Corpus changed before generation publication')
                _protected(target,private=True)
                index_bytes=target.stat().st_size
                result={'generation':generation,'manifest_sha256':validated['manifest_sha256'],
                    'document_count':validated['document_count'],'section_count':validated['section_count'],
                    'corpus_bytes':validated['corpus_bytes'],'index_bytes':index_bytes,
                    'index_ratio':index_bytes/validated['corpus_bytes'],
                    'index_size_sla_met':index_bytes<=4*validated['corpus_bytes'],
                    'relative_links_checked':validated['relative_links_checked'],'updated_at':time.time()}
                _write_json(self.state/'sources.json',{key:row['sha256'] for key,row in validated['documents'].items() if key.startswith('.sources/')})
                _write_json(self.state/'current.json',result)
                self._close_readers()
                self._last_error=None
                # Keep the preceding generation for in-flight readers. We do not
                # delete unknown paths or operator corpus/state files.
                keep={generation,previous['generation'] if previous else ''}
                for child in self.state.iterdir():
                    if re.fullmatch('g-[0-9a-f]{32}',child.name) and child.name not in keep:
                        _protected(child,directory=True,private=True)
                        entries=list(child.iterdir())
                        if len(entries)==1 and entries[0].name=='knowledge_index.db':
                            _protected(entries[0],private=True)
                            try:
                                entries[0].unlink();child.rmdir()
                            except PermissionError:
                                continue  # A Windows reader still owns the prior generation.
                return {**result,'changed_documents':changed,'removed_documents':len(removed),'rebuilt_corruption':corruption}
            except BaseException:
                if not (self.state/'current.json').exists() or self.status().get('generation')!=generation:
                    shutil.rmtree(folder)
                raise

    def request_refresh(self):
        """Schedule hash/link/catalog checks without blocking a retrieval request."""
        with self._background_lock:
            if self._closed or self._background is not None and self._background.is_alive():
                return False
            def update():
                try:
                    self.refresh()
                except Exception as exc:
                    self._last_error=type(exc).__name__+': '+str(exc)[:512]
            self._background=threading.Thread(target=update,name='knowledge-index-refresh',daemon=True)
            self._background.start()
            return True

    def search(self,query,limit=5):
        if not isinstance(query,str) or not query.strip() or len(query)>512 or '\x00' in query:
            raise KnowledgeError('Search requires 1..512 characters of literal text')
        if type(limit) is not int or not 1<=limit<=20:
            raise KnowledgeError('Search result limit must be between one and twenty')
        words=re.findall(r'\w+',query,re.UNICODE)
        if not words or len(words)>32:
            raise KnowledgeError('Search must contain one to thirty-two literal terms')
        expression=' AND '.join('"'+word+'"' for word in words)
        try:
            with self._reader() as (generation,reader):
                rows=reader.execute('''SELECT doc_path,section_title,
                    snippet(fts_index,2,'','', ' … ',32) AS snippet,bm25(fts_index) AS score
                    FROM fts_index WHERE fts_index MATCH ? ORDER BY rank LIMIT ?''',(expression,limit)).fetchall()
                return [{**dict(row),'generation':generation['generation']} for row in rows]
        except (sqlite3.DatabaseError,FileNotFoundError):
            self.request_refresh()
            raise KnowledgeError('Knowledge index unavailable; a background rebuild was requested') from None

    def read(self,doc_path):
        key=_key(doc_path)
        try:
            with self._reader() as (generation,reader):
                row=reader.execute('SELECT sha256 FROM documents WHERE doc_path=?',(key,)).fetchone()
                if row is None:
                    raise KnowledgeError('Document is not in the accepted knowledge catalog')
        except (sqlite3.DatabaseError,FileNotFoundError):
            self.request_refresh()
            raise KnowledgeError('Knowledge index unavailable; a background rebuild was requested') from None
        record,text,_=self._document(key)
        if record['sha256']!=row['sha256']:
            self.request_refresh()
            raise KnowledgeError('Document changed since ratification; refresh is pending')
        return {**record,'text':text,'generation':generation['generation']}

    def status(self):
        result={'enabled':True,'last_error':self._last_error,
                'refresh_running':self._background is not None and self._background.is_alive()}
        try:
            current,_=self._current()
            result.update(current,ready=self._last_error is None and current.get('index_size_sla_met') is True,
                          indexed=True)
        except (OSError,ValueError,sqlite3.DatabaseError):
            result.update(ready=False)
        return result

    def close(self):
        if self._background is not None:
            self._background.join(timeout=30)
            if self._background.is_alive():
                raise KnowledgeError('Knowledge background refresh has not stopped')
        self._closed=True
        self._close_readers()


def provision_knowledge(config):
    """Protect a reviewed existing corpus; never invent, replace or rewrite it.

    Invoked only from the explicitly selected SYSTEM provisioning command. Every
    path is checked before ACL mutation. Administrators retain the ability to
    ratify wiki changes and append archive captures; worker accounts have none.
    """
    from . import windows as win
    win.require_system()
    if not config.enabled:
        raise KnowledgeError('Production deployment requires enabled registered knowledge')
    root=Path(config.source_root).parent
    _ancestors(root)
    win._validate_control_ancestors(root.parent)
    required=[Path(config.source_root),Path(config.wiki_root)]
    for path in required:
        _ordinary(path,directory=True)
    _ordinary(Path(config.manifest_path))
    entries=[root]
    for current,dirs,files in os.walk(root,followlinks=False):
        for name in dirs:
            path=Path(current)/name;_ordinary(path,directory=True);entries.append(path)
        for name in files:
            path=Path(current)/name;_ordinary(path);entries.append(path)
    # Refuse a broad corpus root containing unrelated files before changing ACLs.
    if {path.name for path in root.iterdir()} != {'.sources','wiki','v4.1.2_manifest.json'}:
        raise KnowledgeError('Knowledge root must contain only the two collections and reviewed manifest')
    # Do not follow a replacement placed between traversal and SetFileSecurityW.
    # Provisioning may tighten readable corpus ACLs, but an untrusted writer or
    # owner must first be removed by a protected installer copy, not by racing a
    # model-writable live tree with privileged path-based ACL changes.
    trusted={win.SYSTEM_SID,win.ADMIN_SID}
    for path in entries:
        owner,_,rules=win._acl(path)
        if owner not in trusted or any(sid not in trusted and not flags&8 and mask&0x500D0116
                                      for sid,mask,flags in rules):
            raise KnowledgeError('Provisioning requires an administrator-owned corpus without untrusted writers')
    for path in entries:
        win._set_acl(path,None)
    return {'protected_entries':len(entries),'corpus_preserved':True}
