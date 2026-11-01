# -*- coding: utf-8 -*-
"""Command-line interface: doctor / bench / loop / run / train / serve / export."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from .config import load_config
from .core.engine import Engine
from . import bench as benchmod
from .world.oracle import make_batch, signal_for
from .training.trainer import SkillTrainer


def cmd_doctor(cfg):
    engine = Engine(cfg)
    result = engine.run_all()
    sr = engine.stability_report()
    print("[doctor] tasks=%d solved=%d (%.1f%%) mean_conv=%.3f"
          % (result["summary"]["tasks"], result["summary"]["solved"],
             result["summary"]["solved_rate"] * 100, result["summary"]["mean_convergence"]))
    print("[doctor] stability mhc_healthy=%d naive_healthy=%d collapse_gap=%d"
          % (sr["mhc_healthy"], sr["naive_healthy"], sr["collapse_gap"]))
    print("[doctor] OK")


def cmd_bench(cfg):
    rows = benchmod.run_all()
    print("[bench] skill  recall  precision  fpr    accuracy  f1")
    for r in rows:
        print("[bench] %.2f   %.3f   %.3f      %.3f   %.3f    %.3f" % (
            r["skill"], r["solve_recall"], r["solve_precision"],
            r["false_flag_rate"], r["solve_accuracy"], r["f1"]))
    s = benchmod.summarize(rows)
    print("[bench] recall %.3f->%.3f | precision %.3f->%.3f | fpr %.3f->%.3f | acc %.3f->%.3f | f1 %.3f->%.3f"
          % (s["recall"][0], s["recall"][1], s["precision"][0], s["precision"][1],
             s["fpr"][0], s["fpr"][1], s["accuracy"][0], s["accuracy"][1], s["f1"][0], s["f1"][1]))

    curve = benchmod.run_loop_curve()
    print("[bench] loops  conv    depth  params_eff  mhc_health  collapse_gap")
    for r in curve:
        print("[bench] %5d  %.4f  %5d  %.4f     %.3f        %d" % (
            r["loops"], r["mean_convergence"], r["effective_depth"],
            r["param_efficiency"], r["mhc_health_rate"], r["collapse_gap"]))
    c = benchmod.summarize_curve(curve)
    print("[bench] conv %.3f->%.3f | depth %d->%d | eff %.4f->%.4f | mhc %.2f->%.2f | gap %d->%d"
          % (c["convergence"][0], c["convergence"][1], c["effective_depth"][0],
             c["effective_depth"][1], c["param_efficiency"][0], c["param_efficiency"][1],
             c["mhc_health"][0], c["mhc_health"][1], c["collapse_gap"][0], c["collapse_gap"][1]))


def cmd_loop(cfg, args):
    task = [t for t in make_batch() if t["id"] == args.task]
    if not task:
        raise SystemExit("unknown task: %s" % args.task)
    task = task[0]
    print("[loop] task=%s loops=%d" % (task["id"], args.loops))
    for k in (1, 2, 4, 8, 16, 32):
        if k > args.loops:
            break
        print("[loop]   loops=%2d  convergence=%.4f" % (k, signal_for(task, k)))


def cmd_run(cfg, args):
    engine = Engine(cfg)
    result = engine.run_all(args.loops)
    print("[run] tasks=%d solved=%d mean_conv=%.3f" % (
        result["summary"]["tasks"], result["summary"]["solved"],
        result["summary"]["mean_convergence"]))
    for r in result["rows"]:
        print("[run]   %-14s conv=%.3f conf=%.3f solved=%s" % (
            r["id"], r["convergence"], r["confidence"], r["solved"]))
    if args.out:
        Path(args.out).write_text(engine.render_report(result), encoding="utf-8")
        print("[run] wrote %s" % args.out)


def cmd_stability(cfg, args):
    engine = Engine(cfg)
    for L in (1, 2, 4, 8, 16, 32):
        sr = engine.stability_report(L)
        print("[stability] loops=%2d  mhc_healthy=%d  naive_healthy=%d  gap=%d"
              % (L, sr["mhc_healthy"], sr["naive_healthy"], sr["collapse_gap"]))


def cmd_train(cfg, args):
    t = SkillTrainer(cfg["model"]["skill"], args.lr)
    for i in range(args.epochs):
        step = t.train_step()
        print("[train] step %02d skill=%.3f solved_rate %.3f->%.3f" % (
            i + 1, step["skill"], step["solved_rate_before"], step["solved_rate_after"]))


def cmd_serve(cfg, args):
    from .serving.app import serve
    serve(host=args.host, port=args.port, cfg=cfg)


def cmd_modes(cfg, args):
    print("[modes] normalize=%s mhc=%s loops=%d dim=%d skill=%.2f" % (
        cfg["loop"]["normalize"], cfg["mhc"]["enabled"],
        cfg["loop"]["loops"], cfg["loop"]["dim"], cfg["model"]["skill"]))


def build_parser():
    ap = argparse.ArgumentParser(prog="loopforge",
                                 description="looped transformer + mHC reference implementation")
    ap.add_argument("--recipe", default=None, help="yaml recipe to merge over defaults")
    ap.add_argument("--skill", type=float, default=None, help="model skill 0.0-1.0")
    ap.add_argument("--loops", type=int, default=None, help="loop count (effective depth)")
    sub = ap.add_subparsers(dest="cmd")

    sub.add_parser("doctor")
    sub.add_parser("bench")
    sub.add_parser("modes")

    p = sub.add_parser("loop"); p.add_argument("task"); p.add_argument("--loops", type=int, default=32)
    p = sub.add_parser("run"); p.add_argument("--loops", type=int, default=None); p.add_argument("--out", default=None)
    sub.add_parser("stability")
    p = sub.add_parser("train"); p.add_argument("--epochs", type=int, default=10); p.add_argument("--lr", type=float, default=0.05)
    p = sub.add_parser("export"); p.add_argument("--out", default="report.md")
    p = sub.add_parser("serve"); p.add_argument("--host", default="127.0.0.1"); p.add_argument("--port", type=int, default=8431)
    return ap


def main(argv=None):
    ap = build_parser()
    args = ap.parse_args(argv)
    cfg = load_config(args.recipe)
    if args.skill is not None:
        cfg["model"]["skill"] = args.skill
    if args.loops is not None:
        cfg["loop"]["loops"] = args.loops
    if args.cmd is None:
        ap.print_help()
        return
    dispatch = {
        "doctor": lambda: cmd_doctor(cfg),
        "bench": lambda: cmd_bench(cfg),
        "loop": lambda: cmd_loop(cfg, args),
        "run": lambda: cmd_run(cfg, args),
        "stability": lambda: cmd_stability(cfg, args),
        "train": lambda: cmd_train(cfg, args),
        "serve": lambda: cmd_serve(cfg, args),
        "modes": lambda: cmd_modes(cfg, args),
    }
    dispatch[args.cmd]()


if __name__ == "__main__":
    main()
