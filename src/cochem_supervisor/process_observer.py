"""Independent, bounded per-process RSS/handle history; never imports the pipeline.

Windows handles and POSIX file descriptors are distinct reported measurements.
PID plus native creation time fences history against PID reuse. Observations do
not terminate anything and never infer useful work from process existence.
"""
from __future__ import annotations

import json
import math
from pathlib import Path
import time

import psutil

from .io import write_json


class ProcessObserver:
    def __init__(self, state_file: Path, *, minimum_window_seconds: float=30,
                 rss_growth_bytes: int=128*1024*1024, rss_rate_bytes: float=1024*1024,
                 handle_growth: int=128, handle_rate: float=1):
        self.path=Path(state_file)
        for value in (minimum_window_seconds,rss_growth_bytes,rss_rate_bytes,handle_growth,handle_rate):
            if type(value) not in (int,float) or not math.isfinite(value) or value<=0:
                raise ValueError('Process observation thresholds must be finite positive numbers')
        self.window=minimum_window_seconds
        self.thresholds={'rss_bytes':(rss_growth_bytes,rss_rate_bytes),'handles':(handle_growth,handle_rate)}

    @staticmethod
    def _slope(samples,field):
        values=[(row['at'],row[field]) for row in samples if type(row.get(field)) in (int,float)]
        if len(values)<3:
            return {'sample_count':len(values),'growth':0,'per_second':0,'window_seconds':0,'increasing':False}
        origin=values[0][0]
        points=[(at-origin,value) for at,value in values]
        avg_x=sum(x for x,_ in points)/len(points)
        avg_y=sum(y for _,y in points)/len(points)
        denominator=sum((x-avg_x)**2 for x,_ in points)
        slope=sum((x-avg_x)*(y-avg_y) for x,y in points)/denominator if denominator else 0
        return {'sample_count':len(points),'growth':values[-1][1]-values[0][1],
                'per_second':round(slope,3),'window_seconds':round(values[-1][0]-origin,3),
                'increasing':all(right[1]>=left[1] for left,right in zip(values,values[1:]))}

    def _history(self):
        if not self.path.exists():
            return {}
        if self.path.is_symlink() or not self.path.is_file() or self.path.stat().st_size>512*1024:
            raise ValueError('Process history must be a bounded plain file')
        data=json.loads(self.path.read_text(encoding='utf-8'))
        if not isinstance(data,dict) or data.get('schema')!=1 or not isinstance(data.get('processes'),dict):
            raise ValueError('Invalid process observation history')
        history=data['processes']
        if len(history)>128:
            raise ValueError('Process history exceeds its process bound')
        for key,entry in history.items():
            if not isinstance(key,str) or not isinstance(entry,dict) or not isinstance(entry.get('samples'),list) or len(entry['samples'])>8:
                raise ValueError('Invalid process samples')
            for row in entry['samples']:
                if not isinstance(row,dict) or any(type(row.get(k)) not in (int,float) or not math.isfinite(row[k]) or row[k]<0 for k in ('at','rss_bytes')):
                    raise ValueError('Invalid resource sample')
        return history

    def observe(self,pid: int, *, now: float | None=None) -> dict:
        if type(pid) is not int or pid<=0:
            raise ValueError('A positive controller process ID is required')
        at=time.time() if now is None else now
        if type(at) not in (int,float) or not math.isfinite(at) or at<0:
            raise ValueError('Invalid process observation timestamp')
        history=self._history()
        deadline=time.monotonic()+0.5
        pending=[pid]; seen=set(); measured=[]; alarms=[]; unavailable=[]
        while pending and len(seen)<64 and time.monotonic()<deadline:
            current=pending.pop(0)
            if current in seen:
                continue
            seen.add(current)
            try:
                process=psutil.Process(current)
                created=process.create_time()
                key=f'{current}:{created:.6f}'
                with process.oneshot():
                    rss=process.memory_info().rss
                    handles=process.num_handles() if hasattr(process,'num_handles') else process.num_fds()
                    status=process.status()
                if process.create_time()!=created or not process.is_running():
                    continue
                entry=history.get(key,{'pid':current,'created_at':created,'samples':[]})
                samples=entry['samples']
                if samples and at<samples[-1]['at']:
                    raise ValueError('Process sample clock cannot move backwards')
                # Keep the eight stored samples spread across the required
                # window even when the detector polls several times a second.
                if not samples or at-samples[-1]['at']>=self.window/7:
                    samples.append({'at':at,'rss_bytes':rss,'handles':handles})
                entry['samples']=samples[-8:]
                entry['handle_metric']='windows_handles' if hasattr(process,'num_handles') else 'posix_file_descriptors'
                history[key]=entry
                slopes={field:self._slope(entry['samples'],field) for field in self.thresholds}
                result={'pid':current,'created_at':created,'status':status,'rss_bytes':rss,
                        'handles':handles,'handle_metric':entry['handle_metric'],'slopes':slopes}
                measured.append(result)
                for field,(growth,rate) in self.thresholds.items():
                    trend=slopes[field]
                    if (trend['window_seconds']>=self.window and trend['increasing'] and
                            trend['growth']>=growth and trend['per_second']>=rate):
                        alarms.append({'pid':current,'created_at':created,'metric':field,
                                       'handle_metric':entry['handle_metric'],**trend})
                pending.extend(child.pid for child in process.children(recursive=False)[:64-len(seen)])
            except (psutil.NoSuchProcess,psutil.AccessDenied,psutil.ZombieProcess) as exc:
                unavailable.append({'pid':current,'reason':type(exc).__name__})
        # Keep bounded recent exited histories as evidence for OOM/process death.
        recent={key:item for key,item in history.items() if item['samples'] and at-item['samples'][-1]['at']<=600}
        recent=dict(sorted(recent.items(),key=lambda pair:pair[1]['samples'][-1]['at'],reverse=True)[:128])
        write_json(self.path,{'schema':1,'processes':recent})
        return {'state':'observed' if measured else 'unavailable','root_pid':pid,'sampled_at':at,
                'processes':measured,'alarms':alarms,'unavailable':unavailable,
                'truncated':bool(pending),'history_file':str(self.path),
                'recent_exited':[{'pid':entry['pid'],'created_at':entry['created_at'],
                    'last_sample':entry['samples'][-1],
                    'slopes':{field:self._slope(entry['samples'],field) for field in self.thresholds}}
                    for entry in recent.values() if entry['pid'] not in {item['pid'] for item in measured}][:16]}
