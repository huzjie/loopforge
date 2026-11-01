# Contributing

1. Keep it zero-dependency (stdlib only).
2. Everything deterministic: use `utils/rng.py` seeded helpers.
3. Benchmarks must be monotonic; add a test if you add a metric.
4. Run `python -m unittest discover -s tests -t .` before contributing.
