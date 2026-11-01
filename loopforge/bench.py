# -*- coding: utf-8 -*-
"""Reproducible benchmark: two monotonic dimensions.

Dimension A -- model skill (0.5 random -> 1.0 expert), holding loops fixed:
- solve_recall        (up)
- solve_precision     (up)
- false_flag_rate     (down)
- solve_accuracy      (up)
- f1                  (up)

Dimension B -- loop count (1 -> 32), the "compute-for-params" axis:
- mean_convergence    (up)   real power-iteration convergence to ground truth
- effective_depth     (up)   == loops
- param_efficiency    (up)   == loops / params (more depth per parameter)
- mhc_health_rate     (flat) mHC keeps deep recurrence stable
- collapse_gap        (up)   mHC health minus naive health (why mHC matters)
"""
from __future__ import annotations

from .core.model import Model
from .core.engine import Engine
from .config import load_config
from .world.oracle import make_batch, signal_for
from .utils.rng import stable_float

SKILLS = [0.5, 0.6, 0.7, 0.8, 0.9, 1.0]
LOOPS = [1, 2, 4, 8, 16, 32]
THRESHOLD = 0.9


def _solve_metrics(skill, loops=8):
    m = Model(skill)
    tp = fn = fp = tn = 0
    for task in make_batch():
        signal = signal_for(task, loops)
        noise = stable_float("noise:" + task["id"])
        label = signal >= THRESHOLD
        flagged = m.flag(signal, noise)
        if label and flagged:
            tp += 1
        elif label:
            fn += 1
        elif flagged:
            fp += 1
        else:
            tn += 1
    recall = tp / (tp + fn) if (tp + fn) else 0.0
    precision = tp / (tp + fp) if (tp + fp) else 1.0
    fpr = fp / (fp + tn) if (fp + tn) else 0.0
    accuracy = (tp + tn) / (tp + fn + fp + tn) if (tp + fn + fp + tn) else 0.0
    f1 = 2 * precision * recall / (precision + recall) if (precision + recall) else 0.0
    return recall, precision, fpr, accuracy, f1


def run_all(skills=None):
    skills = skills or SKILLS
    rows = []
    for s in skills:
        recall, precision, fpr, accuracy, f1 = _solve_metrics(s)
        rows.append({
            "skill": s,
            "solve_recall": round(recall, 3),
            "solve_precision": round(precision, 3),
            "false_flag_rate": round(fpr, 3),
            "solve_accuracy": round(accuracy, 3),
            "f1": round(f1, 3),
        })
    return rows


def run_loop_curve(loops=None):
    loops = loops or LOOPS
    cfg = load_config()
    engine = Engine(cfg)
    batch = make_batch()
    n = len(batch)
    mean_params = sum(t["dim"] * t["dim"] + t["dim"] for t in batch) / n
    rows = []
    for L in loops:
        convs = [signal_for(t, L) for t in batch]
        sr = engine.stability_report(L)
        rows.append({
            "loops": L,
            "mean_convergence": round(sum(convs) / n, 4),
            "effective_depth": L,
            "param_efficiency": round(L / mean_params, 4),
            "mhc_health_rate": round(sr["mhc_healthy"] / n, 4),
            "collapse_gap": sr["collapse_gap"],
        })
    return rows


def summarize(rows):
    """Endpoint deltas for the README (skill 0.5 -> 1.0)."""
    first, last = rows[0], rows[-1]
    return {
        "recall": (first["solve_recall"], last["solve_recall"]),
        "precision": (first["solve_precision"], last["solve_precision"]),
        "fpr": (first["false_flag_rate"], last["false_flag_rate"]),
        "accuracy": (first["solve_accuracy"], last["solve_accuracy"]),
        "f1": (first["f1"], last["f1"]),
    }


def summarize_curve(rows):
    first, last = rows[0], rows[-1]
    return {
        "convergence": (first["mean_convergence"], last["mean_convergence"]),
        "effective_depth": (first["effective_depth"], last["effective_depth"]),
        "param_efficiency": (first["param_efficiency"], last["param_efficiency"]),
        "mhc_health": (first["mhc_health_rate"], last["mhc_health_rate"]),
        "collapse_gap": (first["collapse_gap"], last["collapse_gap"]),
    }
