"""Joiner — match grad_oracle predictions against GMGN snapshots within
±tolerance_s timestamp window. Dedupe on first-observation per pre-reg.

Each prediction lands as one row in case_study_01_observations. For each
graduate-oracle prediction, the joiner picks the GMGN snapshot whose
snapshot_at is closest to the prediction's predicted_at, within tolerance.
If no GMGN snapshot is within tolerance, the row still lands but
gmgn_snapshot_at is NULL and gmgn_in_strict_preset is NULL — the absence
is itself data.
"""
import time
from typing import Optional


class Joiner:
    def __init__(self, tolerance_s: int = 120):
        self.tolerance_s = tolerance_s

    def match(self, grad_pred: dict, gmgn_snapshots: list[dict]) -> dict:
        """Return a joined-observation dict combining the grad_oracle prediction
        with the closest GMGN snapshot within ±tolerance_s. The GMGN side may
        be empty; the row still lands."""
        pred_ts = grad_pred["predicted_at"]
        mint = grad_pred["mint"]

        # Find closest GMGN snapshot in tolerance window
        best_snap = None
        best_diff = None
        for snap in gmgn_snapshots:
            diff = abs(snap["snapshot_at"] - pred_ts)
            if diff <= self.tolerance_s and (best_diff is None or diff < best_diff):
                best_snap = snap
                best_diff = diff

        # Build the observation row
        row = {
            "mint": mint,
            "grad_oracle_predicted_at": pred_ts,
            # graduate-oracle side
            "go_grad_prob": grad_pred.get("predicted_prob"),
            "go_grad_prob_bucket": grad_pred.get("grad_prob_bucket"),
            "go_age_bucket": grad_pred.get("age_bucket"),
            "go_runner_prob_2x": grad_pred.get("runner_prob_2x"),
            "go_runner_prob_5x": grad_pred.get("runner_prob_5x"),
            "go_expected_peak_mult": grad_pred.get("expected_peak_mult"),
            # mint_checkpoints feature snapshot (joined at pull time)
            "go_feat_smart_money": grad_pred.get("feat_feature_smart_money"),
            "go_feat_n_whales": grad_pred.get("feat_feature_n_whales"),
            "go_feat_unique_buyers": grad_pred.get("feat_feature_unique_buyers"),
            "go_feat_vsol_velocity": grad_pred.get("feat_feature_vsol_velocity"),
            "go_feat_fee_delegated": grad_pred.get("feat_feature_fee_delegated"),
            "go_feat_creator_n_launches": grad_pred.get("feat_feature_creator_n_launches"),
            "go_feat_creator_5x_rate": grad_pred.get("feat_feature_creator_5x_rate"),
            "go_feat_bundle_pct": grad_pred.get("feat_feature_bundle_pct"),
            "go_feat_dex_paid": grad_pred.get("feat_feature_dex_paid"),
            # GMGN side
            "gmgn_snapshot_at": None,
            "gmgn_in_strict_preset": None,
            "gmgn_progress": None,
            "gmgn_holder_count": None,
            "gmgn_smart_degen_count": None,
            "gmgn_renowned_count": None,
            "gmgn_top70_sniper_hold_rate": None,
            "gmgn_creator_created_open_ratio": None,
            "gmgn_bundler_rate": None,
            "gmgn_rug_ratio": None,
            "gmgn_raw_excerpt": None,
            "join_diff_s": None,
            # outcome (resolved later by resolver.py)
            "outcome_resolved_at": None,
            "outcome_graduated": None,
            "outcome_peak_mult": None,
            "captured_at": int(time.time()),
        }
        if best_snap is not None:
            row["gmgn_snapshot_at"] = best_snap["snapshot_at"]
            row["join_diff_s"] = best_diff
            # Find this mint within best_snap's mints list
            mint_record = None
            for m in best_snap.get("mints", []):
                m_mint = m.get("address") or m.get("mint") or m.get("token_address") or m.get("ca")
                if m_mint == mint:
                    mint_record = m
                    break
            row["gmgn_in_strict_preset"] = 1 if mint_record is not None else 0
            if mint_record is not None:
                # Pull common GMGN fields (best-effort; schema can vary)
                row["gmgn_progress"] = mint_record.get("progress")
                row["gmgn_holder_count"] = mint_record.get("holder_count") or mint_record.get("holders")
                row["gmgn_smart_degen_count"] = mint_record.get("smart_degen_count") or mint_record.get("smart_count")
                row["gmgn_renowned_count"] = mint_record.get("renowned_count")
                row["gmgn_top70_sniper_hold_rate"] = mint_record.get("top70_sniper_hold_rate") or mint_record.get("sniper_hold_rate")
                row["gmgn_creator_created_open_ratio"] = mint_record.get("creator_created_open_ratio")
                row["gmgn_bundler_rate"] = mint_record.get("bundler_rate")
                row["gmgn_rug_ratio"] = mint_record.get("rug_ratio")
                # Save a JSON excerpt for debugging — first 500 chars
                import json as _j
                row["gmgn_raw_excerpt"] = _j.dumps(mint_record)[:500]
        return row
