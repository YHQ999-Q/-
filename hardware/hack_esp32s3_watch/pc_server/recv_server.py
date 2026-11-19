r"""
ESP32-S3 手表 - 电脑端接收服务器

用法（Windows）：
    1. 打开命令提示符，进入这个文件夹
    2. 运行：  python recv_server.py
    3. 看到 "监听 0.0.0.0:8000" 就说明起来了
    4. 手表传过来的照片/录音会自动存到 uploads 文件夹

注意：
    - 电脑和手表必须连同一个 WiFi
    - 防火墙第一次会弹窗问是否允许，选"允许"
    - C:\Users\17606\Downloads\... 里的 UPLOAD_URL 要改成这台电脑的 IP
      查本机 IP：在命令提示符里输入 ipconfig，看"IPv4 地址"
"""

import datetime
import os
from http.server import BaseHTTPRequestHandler, HTTPServer

# 文件存到这里（脚本所在目录下的 uploads 文件夹）
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
SAVE_DIR = os.path.join(BASE_DIR, "uploads")

PORT = 8000


class UploadHandler(BaseHTTPRequestHandler):
    def do_POST(self):
        # 1. 读长度
        try:
            length = int(self.headers.get("Content-Length", 0))
        except ValueError:
            length = 0

        if length <= 0:
            self.send_response(400)
            self.end_headers()
            print("收到空数据，忽略")
            return

        # 2. 读数据
        data = self.rfile.read(length)

        # 3. 根据自定义头决定扩展名
        kind = self.headers.get("X-Data-Type", "bin").lower()
        ext = {"jpeg": ".jpg", "jpg": ".jpg", "wav": ".wav"}.get(kind, ".bin")

        # 4. 存盘
        os.makedirs(SAVE_DIR, exist_ok=True)
        stamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S_%f")[:-3]
        filename = stamp + ext
        path = os.path.join(SAVE_DIR, filename)

        with open(path, "wb") as f:
            f.write(data)

        print(f"[{datetime.datetime.now():%H:%M:%S}] 收到 {kind:5s} "
              f"{length:>7d} 字节  ->  uploads/{filename}")

        # 5. 回 200，ESP32 那边会打印 "HTTP 200"
        self.send_response(200)
        self.send_header("Content-Type", "text/plain")
        self.end_headers()
        self.wfile.write(b"ok")

    # 不打印每个请求的默认日志，保持输出干净
    def log_message(self, fmt, *args):
        pass


def main():
    os.makedirs(SAVE_DIR, exist_ok=True)
    print("=" * 50)
    print(f"保存目录: {SAVE_DIR}")
    print(f"监听 0.0.0.0:{PORT}  (Ctrl+C 退出)")
    print("=" * 50)
    server = HTTPServer(("0.0.0.0", PORT), UploadHandler)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n已停止")
        server.server_close()


if __name__ == "__main__":
    main()
