# -*- coding: utf-8 -*-
"""Runtime configuration with layered defaults (code default < recipe yaml < cli)."""
from __future__ import annotations

import copy

from .utils.yamlish import yaml_loads

# Defaults are deliberately small so a reference run stays interactive
# (<10s on a laptop) while still exercising every code path.
DEFAULTS = {
    "loop": {
        "loops": 8,               # number of recurrent passes (effective depth)
        "dim": 8,                 # state / token dimension
        "normalize": True,        # True = power iteration (converge); False = naive (drift)
    },
    "mhc": {
        "enabled": True,          # manifold-constrained hyper-connections on/off
        "n_streams": 3,           # identity / residual / gate
        "epsilon": 1e-3,          # mixing-weight manifold bound [eps, 1-eps]
        "alpha": [0.5, 0.3, 0.2], # constrained mixing weights (unit-sum)
    },
    "stability": {
        "norm_lo": 0.5,           # healthy activation-norm band
        "norm_hi": 2.0,
    },
    "model": {
        "skill": 0.5,             # 0.0 (adversarial) .. 1.0 (expert); trainable
    },
    "engine": {
        "min_confidence": 0.5,    # convergence-confidence threshold to flag
        "convergence_threshold": 0.9,  # cos(v_loops, v*) above which a task is "solved"
    },
    "report": {
        "format": "markdown",     # markdown | json
        "out_dir": "reports",
    },
}


def _merge(base, override):
    out = copy.deepcopy(base)
    for k, v in (override or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _merge(out[k], v)
        else:
            out[k] = v
    return out


def load_config(recipe_path=None):
    """Return the merged config dict. A recipe (yaml subset) overrides defaults."""
    cfg = copy.deepcopy(DEFAULTS)
    if recipe_path:
        with open(recipe_path, "r", encoding="utf-8") as f:
            raw = yaml_loads(f.read())
        cfg = _merge(cfg, raw)
    return cfg
