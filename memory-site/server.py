# -*- coding: utf-8 -*-
"""旧版记忆站（Bee 风格）· 本地静态镜像 + 原站 API 代理

这一版网页来自队友原来的站点，界面不改；本服务只做两件事：
  1. 把镜像里的 HTML/CSS/JS 原样发出去；
  2. 把页面发出的 /api/* 请求转发到原来的线上站点。

它不写任何本地数据库，也不参与教学 Agent 的策略决策。
端口用 5002，避开 5001 依赖的本机感知服务（OCR/ASR，默认 5000）。
"""

from __future__ import annotations

import os
import urllib.error
import urllib.request

from flask import Flask, Response, jsonify, request, send_from_directory

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
MIRROR = os.path.join(BASE_DIR, "mirror")
REAL_SITE = os.environ.get(
    "METAMIND_MEMORY_SITE", "https://bee-agent-akmalali.treasuredbluebird16.chatgpt.site"
)
PORT = int(os.environ.get("MEMORY_SITE_PORT", "5002"))

app = Flask(__name__, static_folder=None)


def _file(name: str, mimetype: str | None = None):
    response = send_from_directory(MIRROR, name, mimetype=mimetype)
    response.headers["Cache-Control"] = "no-store, max-age=0"
    return response


@app.get("/")
def index():
    return _file("index.html")


@app.get("/assets/<path:asset>")
def assets(asset: str):
    return send_from_directory(os.path.join(MIRROR, "assets"), asset)


@app.get("/__teach_inject.js")
def inject():
    return _file("inject.js", "application/javascript")


@app.route("/api/<path:path>", methods=["GET", "POST", "OPTIONS"])
def proxy(path: str):
    if request.method == "OPTIONS":
        return Response("", 204)
    url = f"{REAL_SITE}/api/{path}"
    if request.query_string:
        url += "?" + request.query_string.decode()
    data = request.get_data() if request.method != "GET" else None
    req = urllib.request.Request(url, data=data, method=request.method)
    content_type = request.headers.get("Content-Type")
    if content_type:
        req.add_header("Content-Type", content_type)
    req.add_header("User-Agent", "Mozilla/5.0")
    try:
        with urllib.request.urlopen(req, timeout=60) as upstream:
            return Response(
                upstream.read(),
                status=upstream.status,
                content_type=upstream.headers.get("Content-Type", "application/json"),
            )
    except urllib.error.HTTPError as exc:
        return Response(
            exc.read(),
            status=exc.code,
            content_type=exc.headers.get("Content-Type", "application/json"),
        )
    except Exception as exc:  # keep the page usable when offline
        return jsonify({"error": f"线上接口不可达：{exc}"}), 502


@app.get("/health")
def health():
    return jsonify({"ok": True, "service": "memory-site", "upstream": REAL_SITE})


@app.get("/<path:path>")
def spa(path: str):
    if path.startswith("api/"):
        return jsonify({"error": "not found"}), 404
    full = os.path.join(MIRROR, path)
    if os.path.isfile(full):
        return send_from_directory(MIRROR, path)
    return _file("index.html")


if __name__ == "__main__":
    print(f"旧版记忆站：http://127.0.0.1:{PORT}/")
    app.run(host="0.0.0.0", port=PORT, threaded=True)
