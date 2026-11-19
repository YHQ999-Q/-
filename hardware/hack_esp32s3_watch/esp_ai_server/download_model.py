"""
下载 faster-whisper 模型到本地（绕开 huggingface_hub 的 SSL 问题）

用法：
    cd E:\\hack_esp32s3_watch\\esp_ai_server
    .\\venv\\Scripts\\python.exe download_model.py

下完会存到 models\\faster-whisper-small\\，app.py 会自动优先用本地目录。
"""

import os
import sys
import urllib.request

REPO = "Systran/faster-whisper-small"
BASE = "https://hf-mirror.com"
FILES = ["config.json", "tokenizer.json", "vocabulary.txt", "model.bin"]

OUT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "models", "faster-whisper-small")


def download(name: str) -> None:
    url = f"{BASE}/{REPO}/resolve/main/{name}"
    dest = os.path.join(OUT_DIR, name)
    tmp = dest + ".part"

    have = os.path.getsize(tmp) if os.path.exists(tmp) else 0
    req = urllib.request.Request(url)
    if have:
        req.add_header("Range", f"bytes={have}-")

    with urllib.request.urlopen(req, timeout=60) as r:
        total = r.headers.get("Content-Length")
        total = int(total) + have if total else None
        mode = "ab" if have else "wb"
        done = have
        last = 0
        with open(tmp, mode) as f:
            while True:
                chunk = r.read(1024 * 256)
                if not chunk:
                    break
                f.write(chunk)
                done += len(chunk)
                if total and done - last > total // 20:
                    last = done
                    pct = done * 100 // total
                    sys.stdout.write(f"\r  {name}: {pct}%  ({done/1048576:.1f}/{total/1048576:.1f} MB)")
                    sys.stdout.flush()
    os.replace(tmp, dest)
    print(f"\r  {name}: 完成 ({os.path.getsize(dest)/1048576:.1f} MB)")


def main():
    os.makedirs(OUT_DIR, exist_ok=True)
    print(f"下载 {REPO} -> {OUT_DIR}")
    for f in FILES:
        if os.path.exists(os.path.join(OUT_DIR, f)):
            print(f"  {f}: 已存在，跳过")
            continue
        download(f)
    print("全部完成")


if __name__ == "__main__":
    main()
