# -*- coding: utf-8 -*-
"""Standard-library HTTP API (no external server dependency).

Endpoints:
- GET  /health          -> {"ok": true, "name": "loopforge"}
- GET  /v1/info         -> config summary (loops, dim, mhc, skill)
- GET  /v1/run          -> full task run + aggregate summary
- GET  /v1/bench        -> skill table + loop curve
- POST /v1/run          -> {"loops": N} -> full task run at N loops
- GET  /v1/stability    -> mHC vs naive deep-recurrence stability
"""
from __future__ import annotations

import json
from http.server import BaseHTTPRequestHandler, HTTPServer

from ..core.engine import Engine
from .. import bench as benchmod


def make_handler(cfg):
    engine = Engine(cfg)

    class Handler(BaseHTTPRequestHandler):
        def _send(self, code, obj):
            body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            if self.path == "/health":
                self._send(200, {"ok": True, "name": "loopforge", "version": "0.1.0"})
            elif self.path == "/v1/info":
                self._send(200, {
                    "name": "loopforge", "version": "0.1.0",
                    "loops": cfg["loop"]["loops"], "dim": cfg["loop"]["dim"],
                    "normalize": cfg["loop"]["normalize"],
                    "mhc_enabled": cfg["mhc"]["enabled"],
                    "skill": cfg["model"]["skill"],
                })
            elif self.path == "/v1/run":
                self._send(200, engine.run_all())
            elif self.path == "/v1/bench":
                rows = benchmod.run_all()
                curve = benchmod.run_loop_curve()
                self._send(200, {
                    "skill_table": rows, "skill_summary": benchmod.summarize(rows),
                    "loop_curve": curve, "curve_summary": benchmod.summarize_curve(curve),
                })
            elif self.path == "/v1/stability":
                self._send(200, engine.stability_report())
            else:
                self._send(404, {"error": "not found"})

        def do_POST(self):
            if self.path == "/v1/run":
                ln = int(self.headers.get("Content-Length", 0))
                data = json.loads(self.rfile.read(ln).decode("utf-8"))
                loops = data.get("loops")
                self._send(200, engine.run_all(loops))
            else:
                self._send(404, {"error": "not found"})

        def log_message(self, *args):
            pass

    return Handler


def serve(host="127.0.0.1", port=8431, cfg=None):
    if cfg is None:
        from ..config import load_config
        cfg = load_config()
    handler = make_handler(cfg)
    srv = HTTPServer((host, port), handler)
    print("[serve] loopforge listening on http://%s:%d" % (host, port))
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        srv.server_close()
