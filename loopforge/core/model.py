# -*- coding: utf-8 -*-
"""The skill-parametrized model (the reference stand-in for Loop-9B).

It turns a *skill* (0.0 = adversarial, 0.5 = random, 1.0 = expert) and an
oracle *signal* into a deterministic confidence, so the whole pipeline is
reproducible and the benchmark is monotonic.

The confidence formula is the key idea:

    boost   = (skill - 0.5) * 2            # -1 .. +1, zero at random
    conf    = 0.5 + boost*(signal-0.5) + (1-|boost|)*(noise-0.5)

- skill = 0.5  -> boost = 0     -> conf = noise          (coin flip)
- skill = 1.0  -> boost = +1    -> conf = signal         (perfect)
- skill = 0.0  -> boost = -1    -> conf = 1 - signal     (anti-correlated)

Here ``signal`` is the true convergence of the looped block (cos similarity to
the ground-truth eigenvector). A real model does not "know" it; it approximates
it, and training raises that fidelity. We collapse that into one scalar.
"""
from __future__ import annotations

from ..utils.linalg import clamp


class Model:
    def __init__(self, skill=0.5):
        self.skill = clamp(skill, 0.0, 1.0)

    def boost(self):
        return (self.skill - 0.5) * 2.0

    def confidence(self, signal, noise):
        b = self.boost()
        return clamp(0.5 + b * (signal - 0.5) + (1.0 - abs(b)) * (noise - 0.5), 0.0, 1.0)

    def flag(self, signal, noise, min_conf=0.5):
        return self.confidence(signal, noise) >= min_conf
