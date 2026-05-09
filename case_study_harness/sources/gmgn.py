"""GMGN source adapter.

Wraps `gmgn-cli market trenches --chain sol --type new_creation
--filter-preset strict --raw` as a subprocess; captures full JSON
response per poll. Each call returns the current snapshot of the
strict-preset list with timestamp.

Pre-reg constraint: 60s poll cadence; frozen at Case Study 01
commit 5bc8f33. No parameter tweaks during collection window.

Failure handling: subprocess errors return empty snapshot with
error noted; never crashes the harness loop.
"""
import json
import subprocess
import time
from typing import Optional


class GmgnSource:
    name = "gmgn"

    def __init__(self,
                 chain: str = "sol",
                 type_arg: str = "new_creation",
                 filter_preset: str = "strict",
                 cli_path: str = "gmgn-cli",
                 timeout_s: int = 30):
        self.chain = chain
        self.type_arg = type_arg
        self.filter_preset = filter_preset
        self.cli_path = cli_path
        self.timeout_s = timeout_s

    def snapshot(self) -> dict:
        """Run gmgn-cli; return {'snapshot_at', 'mints': [...], 'error': str or None}.
        Each mint dict in 'mints' is the raw GMGN payload for that mint."""
        cmd = [self.cli_path, "market", "trenches",
               "--chain", self.chain,
               "--type", self.type_arg,
               "--filter-preset", self.filter_preset,
               "--raw"]
        snapshot_at = int(time.time())
        try:
            p = subprocess.run(cmd, capture_output=True, text=True,
                               timeout=self.timeout_s)
        except subprocess.TimeoutExpired:
            return {"snapshot_at": snapshot_at, "mints": [],
                    "error": f"timeout after {self.timeout_s}s"}
        except FileNotFoundError:
            return {"snapshot_at": snapshot_at, "mints": [],
                    "error": f"{self.cli_path} not found in PATH"}
        if p.returncode != 0:
            return {"snapshot_at": snapshot_at, "mints": [],
                    "error": f"cli returncode={p.returncode}; stderr={p.stderr[:500]}"}
        try:
            data = json.loads(p.stdout)
        except json.JSONDecodeError as e:
            return {"snapshot_at": snapshot_at, "mints": [],
                    "error": f"JSON parse failed: {e}"}
        # gmgn-cli market trenches returns a dict keyed by trench category:
        #   {"completed": [...], "new_creation": [...], "pump": [...]}
        # Even with --type new_creation, all three keys are present; only
        # the matching array is populated. Pull our configured type's array
        # if present; otherwise fall back to legacy schemas; finally flatten
        # all category arrays.
        if isinstance(data, list):
            mints = data
        elif isinstance(data, dict):
            mints = None
            if self.type_arg and self.type_arg in data and isinstance(data[self.type_arg], list):
                mints = data[self.type_arg]
            if not mints:
                mints = data.get("data") or data.get("mints") or data.get("rank")
            if not mints:
                # Last resort: flatten any list-valued top-level keys.
                flat = []
                for v in data.values():
                    if isinstance(v, list):
                        flat.extend(v)
                mints = flat
        else:
            mints = []
        return {"snapshot_at": snapshot_at, "mints": mints, "error": None}
