# -*- coding: utf-8 -*-
"""Engine: loop a block -> hyper-connect -> measure stability -> decide."""
from __future__ import annotations

from .model import Model
from .loop_block import LoopedBlock
from .mhc import HyperConnections
from .stability import healthy, activation_norm
from ..utils.linalg import cos
from ..utils.rng import stable_float
from ..world.oracle import make_batch, ground_truth


class Engine:
    def __init__(self, cfg):
        self.cfg = cfg
        self.model = Model(cfg["model"]["skill"])
        self.mhc = HyperConnections(
            alpha=cfg["mhc"].get("alpha"),
            epsilon=cfg["mhc"].get("epsilon", 1e-3),
            n_streams=cfg["mhc"].get("n_streams", 3),
        ) if cfg["mhc"].get("enabled", True) else None
        self.norm_lo = cfg["stability"]["norm_lo"]
        self.norm_hi = cfg["stability"]["norm_hi"]

    # -- per-task execution -------------------------------------------------
    def run_task(self, task, loops=None):
        """Run the looped block over one task; return convergence + confidence."""
        loops = loops or self.cfg["loop"]["loops"]
        normalize = self.cfg["loop"].get("normalize", True)
        matrix = task["matrix"]
        block = LoopedBlock(matrix, normalize=normalize)
        v, _ = block.forward(task["init"], loops)
        v_star = ground_truth(task)
        convergence = cos(v, v_star)
        noise = stable_float("noise:" + task["id"])
        confidence = self.model.confidence(convergence, noise)
        threshold = self.cfg["engine"]["convergence_threshold"]
        solved = convergence >= threshold
        flagged = self.model.flag(convergence, noise,
                                  self.cfg["engine"]["min_confidence"])
        return {
            "id": task["id"],
            "loops": loops,
            "convergence": round(convergence, 4),
            "confidence": round(confidence, 4),
            "solved": solved,
            "flagged": flagged,
            "params": block.params,
        }

    def run_all(self, loops=None):
        """Run every task; return per-task rows plus an aggregate summary."""
        rows = [self.run_task(t, loops) for t in make_batch()]
        solved = sum(1 for r in rows if r["solved"])
        flagged = sum(1 for r in rows if r["flagged"])
        n = len(rows)
        return {
            "rows": rows,
            "summary": {
                "tasks": n,
                "solved": solved,
                "solved_rate": round(solved / n, 4),
                "flagged": flagged,
                "mean_convergence": round(sum(r["convergence"] for r in rows) / n, 4),
            },
        }

    # -- stability: mHC vs naive -------------------------------------------
    def stability_report(self, loops=None):
        """Compare deep-recurrence stability with vs without mHC."""
        loops = loops or self.cfg["loop"]["loops"]
        mhc_healthy = 0
        naive_healthy = 0
        n = 0
        for task in make_batch():
            n += 1
            matrix = task["matrix"]
            # with mHC: constrained hyper-connected recurrence
            if self.mhc is not None:
                h_mhc, _ = self.mhc.forward(task["init"], matrix, loops)
            else:
                h_mhc = list(task["init"])
            # without mHC: naive un-normalized recurrence (drifts)
            naive = LoopedBlock(matrix, normalize=False)
            h_naive, _ = naive.forward(task["init"], loops)
            if healthy(h_mhc, self.norm_lo, self.norm_hi):
                mhc_healthy += 1
            if healthy(h_naive, self.norm_lo, self.norm_hi):
                naive_healthy += 1
        return {
            "loops": loops,
            "mhc_healthy": mhc_healthy,
            "naive_healthy": naive_healthy,
            "collapse_gap": mhc_healthy - naive_healthy,
        }

    def render_report(self, result):
        rows = result["rows"]
        s = result["summary"]
        if self.cfg["report"]["format"] == "json":
            import json
            return json.dumps(result, ensure_ascii=False, indent=2)
        lines = [
            "# Loopforge run report",
            "",
            "| task | loops | convergence | confidence | solved | flagged |",
            "|---|---|---|---|---|---|",
        ]
        for r in rows:
            lines.append("| %s | %d | %.3f | %.3f | %s | %s |" % (
                r["id"], r["loops"], r["convergence"], r["confidence"],
                "yes" if r["solved"] else "no", "yes" if r["flagged"] else "no"))
        lines += [
            "",
            "Summary: %d tasks, %d solved (%.1f%%), mean convergence %.3f"
            % (s["tasks"], s["solved"], s["solved_rate"] * 100, s["mean_convergence"]),
        ]
        return "\n".join(lines)
