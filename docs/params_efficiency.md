# Parameter Efficiency

The "compute-for-params" trade is quantified as **parameter efficiency**:

    param_efficiency = effective_depth / parameter_count

A weight-tied block has a FIXED parameter count, so this ratio grows linearly
with the loop count. In the reference benchmark it rises from 0.0085 (1 pass)
to 0.2712 (32 passes) on the same parameters — depth per parameter increases
32x with no added weights. This is the concrete lever behind Loop-9B's claim of
approaching a 27B model with ~9B parameters.
