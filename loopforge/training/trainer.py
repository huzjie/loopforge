# -*- coding: utf-8 -*-
"""Skill trainer: a monotonic, deterministic training loop.

Training a real model raises the fidelity with which it recovers the oracle
signal. Here that is represented by monotonically increasing ``skill``; each
step reports the resulting solve rate so the improvement is visible.
"""
from __future__ import annotations

from ..core.model import Model
from ..core.engine import Engine
from ..config import load_config


class SkillTrainer:
    def __init__(self, skill=0.5, lr=0.05):
        self.skill = skill
        self.lr = lr

    def evaluate(self, skill=None):
        cfg = load_config()
        cfg["model"]["skill"] = self.skill if skill is None else skill
        engine = Engine(cfg)
        result = engine.run_all()
        return result["summary"]

    def train_step(self):
        """One training step: skill increases monotonically toward 1.0."""
        before = self.evaluate()
        self.skill = min(1.0, self.skill + self.lr)
        after = self.evaluate()
        return {"skill": self.skill,
                "solved_rate_before": before["solved_rate"],
                "solved_rate_after": after["solved_rate"]}
