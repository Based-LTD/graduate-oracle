"""Source adapters — read-only pulls from upstream feeds.

Each adapter provides a `pull(since_ts)` or `snapshot()` method returning
list[dict]. Future studies add new source modules here without touching
joiner.py / resolver.py / run_study.py.
"""
