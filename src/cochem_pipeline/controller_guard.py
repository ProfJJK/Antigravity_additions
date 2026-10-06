"""Measure the Warden itself against its strict 50 MiB / 5% polling budget.

These are readiness/admission limits, not a promise that a Python allocation
cannot cross the boundary. Crossing it holds new work without killing the
controller and losing the diagnostic or entering an automatic restart loop.
"""
from __future__ import annotations

import math
import time

import psutil


def assess_controller(measurements):
    reasons = []
    rss = measurements.get('rss_mb')
    cpu = measurements.get('cpu_percent')
    for name, value, maximum in (('resident memory',rss,50),('CPU utilization',cpu,5)):
        if type(value) not in (int,float) or not math.isfinite(value) or value < 0:
            reasons.append('Warden '+name+' telemetry is unavailable')
        elif value > maximum:
            reasons.append('Warden '+name+' exceeds its strict '+str(maximum)+(' MiB' if name=='resident memory' else '%')+' budget')
    return {'ready':not reasons, 'strict_srs_ready':not reasons,
            'action':'pause_admission' if reasons else 'none', 'reasons':reasons,
            'memory_limit_mb':50,'cpu_limit_percent':5,'measurements':measurements}


class ControllerMonitor:
    def __init__(self):
        self.process = psutil.Process()
        self.latest = None
        self.sampled_at = None

    def sample(self, *, force=False):
        now = time.monotonic()
        if not force and self.latest is not None and now-self.sampled_at < 1:
            return self.latest
        measured = {'pid':self.process.pid,'rss_mb':None,'private_mb':None,'cpu_percent':None,
                    'source':'psutil.Process.memory_info/cpu_percent/cpu_affinity',
                    'cpu_scope':'process CPU divided by its actual allowed logical CPU count'}
        try:
            info = self.process.memory_info()
            measured['rss_mb'] = info.rss / 1048576
            private = getattr(info,'private',None)
            measured['private_mb'] = None if private is None else private / 1048576
            affinity = self.process.cpu_affinity()
            measured['allowed_logical_processors'] = affinity
            if not affinity:
                raise ValueError('Controller has no allowed logical CPUs')
            # Subsequent samples include all work since the previous tick;
            # measuring only another idle sleep would conceal controller CPU.
            measured['cpu_percent'] = self.process.cpu_percent(
                interval=.05 if self.latest is None else None)/len(affinity)
        except (psutil.Error, OSError, ValueError, AttributeError) as exc:
            measured['error'] = type(exc).__name__
        self.latest = {**assess_controller(measured),'checked_at':time.time()}
        self.sampled_at = time.monotonic()
        return self.latest
