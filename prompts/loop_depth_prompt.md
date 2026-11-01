# Loop Depth Selection

Given a compute budget and a target task, choose the loop count (effective
depth). Consider:

- **Diminishing returns**: convergence/accuracy saturates; report where.
- **Cost**: cost scales linearly with loop count, params stay constant.
- **Stability**: beyond some depth, unconstrained recurrence drifts.

Output: recommended loop count, expected gain, and the stability mechanism that
must accompany it (e.g. normalization or hyper-connections).
