"""Case Study Harness — reusable comparison instrumentation.

Pre-registered scope at graduate-oracle commit 5bc8f33 (2026-05-08).
Phase 2 scaffold ships per Phase 2 implementer ask. Idle until configured
start_after event fires; runs a frozen-criteria comparison study; outputs
to a dedicated sqlite table separate from production scoring tables.

Per pre-reg:
  - No live rules toggle
  - No production scoring code touched
  - Pure read-only instrumentation against production DB + upstream API

Future studies (02+) reuse this harness with one TOML config + one
pre-reg writeup. Each study extends the receipts moat without
artisanal rebuilding.
"""
