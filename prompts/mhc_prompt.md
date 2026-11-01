# Manifold-Constrained Hyper-Connections

Design a hyper-connected block that carries multiple feature streams (identity,
residual, gate) and mixes them with weights constrained on a manifold.

Requirements:
1. Mixing weights are bounded away from 0 and 1 (no stream dominates or dies).
2. Mixing weights are normalized (unit sum).
3. The mixed state is projected back onto a stable manifold (e.g. the unit
   sphere) so deep recurrence does not drift.

Explain why each constraint is necessary and what breaks if it is removed.
