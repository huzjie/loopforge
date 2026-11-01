# Signal-Propagation Stability Diagnostic

Given a deep recurrent block, diagnose whether signal propagation stays healthy:

1. **Activation norm**: is the state norm staying in a sane band?
2. **Drift**: does the norm grow/shrink geometrically with depth?
3. **Collapse**: does the state lose variance (all entries equalize)?
4. **Gradient**: does the Jacobian of the recurrence have spectral radius ~1?

Prescribe the minimal fix (normalization, constrained hyper-connections, or a
different transition map) for each symptom.
