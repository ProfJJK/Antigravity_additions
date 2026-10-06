"""Persist fixed diagnostic categories without copying arbitrary CLI stderr."""
import re


def native_failure_summary(stderr: str) -> str:
    text=stderr[:16384].casefold()
    patterns=(
        ('Subscription authentication failed',r'not logged in|unauthorized|authentication|expired token|\b401\b'),
        ('Provider quota or rate limit reached',r'quota|rate.?limit|usage limit|credit balance|\b429\b'),
        ('Provider service unavailable',r'service unavailable|bad gateway|connection (?:reset|refused)|\b50[234]\b'),
        ('Unknown native CLI argument or output protocol',r'(?:unknown|unrecognized|unexpected|invalid).{0,40}(?:argument|option|flag|protocol)'),
        ('Host resources exhausted: out of memory or disk full',r'out of memory|cannot allocate memory|no space left|disk full'),
        ('Native CLI permission denied',r'permission denied|access denied'),
    )
    for summary,pattern in patterns:
        if re.search(pattern,text):
            return summary
    return 'Unclassified native CLI failure; protected logs require diagnosis'
