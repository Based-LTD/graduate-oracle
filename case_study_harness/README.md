# Case Study Harness

Reusable comparison instrumentation for graduate-oracle commercial-receipts studies. Parameterized adapter pattern; future studies (02+) reuse the same harness with one TOML config + one pre-reg writeup per study.

**Scope frozen at:** [`docs/research/case_study_01_gmgn_comparison_prereg.md`](../docs/research/case_study_01_gmgn_comparison_prereg.md) (commit `5bc8f33`, 2026-05-08).

## Source tree

```
case_study_harness/
├── __init__.py
├── README.md                     ← this file
├── sources/
│   ├── __init__.py
│   ├── grad_oracle.py            ← read-only sqlite read on /data/data.sqlite
│   └── gmgn.py                   ← gmgn-cli subprocess wrapper
├── joiner.py                     ← ±tolerance_s mint matching across sources
├── resolver.py                   ← grace-window outcome resolution
├── run_study.py                  ← config-driven daemon entry point
└── configs/
    └── study_01_gmgn.toml        ← Case Study 01 frozen config
```

## Design (frozen scope)

- **Read-only against production DB.** All reads from `/data/data.sqlite` use `?mode=ro` URI to prevent accidental writes.
- **Separate output DB.** Observations land in `/data/case_studies.sqlite` — a dedicated file, never the production scoring DB.
- **Idle until trigger.** Daemon starts in trigger-wait state; checks current time against `start_at_ts` from config every 60s; only begins collection once that timestamp passes.
- **No early stops; no parameter tweaks.** Once collection starts, runs the configured `duration_s` window. After collection, runs resolver-only mode for `grace_window_s` seconds. Then exits cleanly.
- **No live rules toggle. No production scoring code touched.** Pure instrumentation.

## How the harness extends to Studies 02+

Add a new source adapter under `sources/` (e.g., `sources/pump_fun.py` for Study 02). Add a new TOML config under `configs/` (e.g., `study_02_pumpfun.toml`). Run with `--config configs/study_02_pumpfun.toml`. No changes to `joiner.py`, `resolver.py`, or `run_study.py` should be needed — the comparison logic is source-agnostic.

The output table per study is parameterized via `[output].table_name` so studies write to parallel tables (`case_study_01_observations`, `case_study_02_observations`, …).

## Running

```bash
# Local development run (requires gmgn-cli on PATH and /data/data.sqlite readable):
python3 -m case_study_harness.run_study \
    --config case_study_harness/configs/study_01_gmgn.toml

# Production deployment: invoked via supervisord on graduate-oracle.fly.dev.
# Daemon enters trigger-wait state immediately; waits for start_at_ts;
# then runs collection (48h) + grace (24h); exits cleanly.
```

## Public-mirror note

This source tree is a **public mirror** of the deployed harness code. The canonical-running version lives in the deployed image at `pump-jito-sniper/case_study_harness/`. Both copies are kept byte-identical at commit time; the public mirror exists for receipts-trail verifiability ("anyone can verify the harness existed at commit time and matches what's deployed").

This is the publish-then-post discipline applied to the harness source itself — same shape as drafts predating posts and diagnoses predating fixes.

## Cross-references

- Pre-reg: [`docs/research/case_study_01_gmgn_comparison_prereg.md`](../docs/research/case_study_01_gmgn_comparison_prereg.md)
- Branch templates: [`docs/research/case_study_01_gmgn_results_branch_a_template.md`](../docs/research/case_study_01_gmgn_results_branch_a_template.md), [`branch_b_template.md`](../docs/research/case_study_01_gmgn_results_branch_b_template.md), [`branch_c_template.md`](../docs/research/case_study_01_gmgn_results_branch_c_template.md)
- BACKLOG entry: [`BACKLOG.md`](../BACKLOG.md) "Case Study 01"
