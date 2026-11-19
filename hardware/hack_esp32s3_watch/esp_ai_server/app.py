"""
ESP32-S3 手表 - AI 后端服务
  · RapidOCR      本地中文文字识别（纯 pip，不用装 Tesseract）
  · faster-whisper 本地语音转文字（纯 pip，CPU 上 int8 量化跑得动）
  · 通义千问 qwen-vl-max  多模态解析黑板图形/公式（可选，要 API Key）

启动：
    cd E:\\hack_esp32s3_watch\\esp_ai_server
    .\\venv\\Scripts\\Activate.ps1
    $env:DASHSCOPE_API_KEY="sk-xxxx"     # 可选，不填就只做本地 OCR
    python app.py

接口（ESP32 直接 POST 原始二进制，不需要 multipart）：
    POST /upload         按请求头 X-Data-Type 自动分发（jpeg / wav）
    POST /upload_image   图片 -> OCR (+可选多模态解析)
    POST /upload_audio   音频 -> 转文字
    GET  /health         健康检查
"""

import os

# HuggingFace 国内镜像，必须在导入 faster_whisper 之前设置
os.environ.setdefault("HF_ENDPOINT", "https://hf-mirror.com")
# 新版 huggingface_hub 默认用 Xet 传输，SSL 不稳时容易中断，关掉走普通 HTTP
os.environ.setdefault("HF_HUB_DISABLE_XET", "1")

import base64
import datetime

import cv2
import numpy as np
from flask import Flask, jsonify, request

# ------------------------------------------------------------------
# 兼容补丁：PyAV 19 移除了 av.open(metadata_errors=...) 参数，
# 但 faster-whisper 1.2.1 还在传，会报 TypeError。
# 这里包一层把该参数丢掉（默认行为就是忽略元数据错误），不影响解码。
# ------------------------------------------------------------------
import av as _av
_av_open_orig = _av.open


def _av_open_compat(*args, **kwargs):
    kwargs.pop("metadata_errors", None)
    return _av_open_orig(*args, **kwargs)


_av.open = _av_open_compat

app = Flask(__name__)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
UPLOAD_DIR = os.path.join(BASE_DIR, "uploads")
os.makedirs(UPLOAD_DIR, exist_ok=True)

# 优先用本地已下载的模型目录（download_model.py 下好的），没有才按名字联网拉
_LOCAL_MODEL_DIR = os.path.join(BASE_DIR, "models", "faster-whisper-small")
_DEFAULT_MODEL = _LOCAL_MODEL_DIR if os.path.isdir(_LOCAL_MODEL_DIR) else "small"
ASR_MODEL_NAME = os.environ.get("ASR_MODEL", _DEFAULT_MODEL)
DASHSCOPE_API_KEY = os.environ.get("DASHSCOPE_API_KEY", "").strip()

# ------------------------------------------------------------------
# 懒加载：第一次用到才加载，启动快，模型下载失败也不影响 /health
# ------------------------------------------------------------------
_ocr = None
_asr = None
_vlm_client = None


def get_ocr():
    global _ocr
    if _ocr is None:
        print("[OCR] 加载 RapidOCR ...")
        from rapidocr_onnxruntime import RapidOCR
        _ocr = RapidOCR()
        print("[OCR] 就绪")
    return _ocr


def get_asr():
    global _asr
    if _asr is None:
        print(f"[ASR] 加载 faster-whisper '{ASR_MODEL_NAME}'（首次会下载模型）...")
        from faster_whisper import WhisperModel
        _asr = WhisperModel(ASR_MODEL_NAME, device="cpu", compute_type="int8")
        print("[ASR] 就绪")
    return _asr


def get_vlm():
    """通义千问多模态客户端；没配 Key 就返回 None"""
    global _vlm_client
    if not DASHSCOPE_API_KEY:
        return None
    if _vlm_client is None:
        import openai
        _vlm_client = openai.OpenAI(
            api_key=DASHSCOPE_API_KEY,
            base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
        )
        print("[VLM] 通义千问客户端就绪")
    return _vlm_client


# ------------------------------------------------------------------
# 工具函数
# ------------------------------------------------------------------
def read_raw_bytes() -> bytes:
    """兼容 multipart 表单和 ESP32 的原始二进制流"""
    # multipart 表单：从文件字段取
    if request.mimetype and request.mimetype.startswith("multipart/"):
        if request.files:
            return next(iter(request.files.values())).read()
        return b""
    # 其余情况一律拿原始 body（parse_form_data=False 防止被表单解析吃掉）
    return request.get_data(cache=True, parse_form_data=False)


def save_file(data: bytes, ext: str) -> str:
    stamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S_%f")[:-3]
    path = os.path.join(UPLOAD_DIR, stamp + ext)
    with open(path, "wb") as f:
        f.write(data)
    return path


def run_vlm_analysis(image_bytes: bytes) -> str:
    """把图片丢给通义千问，解析公式和几何图形"""
    client = get_vlm()
    if client is None:
        return "(未配置 DASHSCOPE_API_KEY，跳过图形/公式解析)"

    b64 = base64.b64encode(image_bytes).decode("utf-8")
    try:
        resp = client.chat.completions.create(
            model="qwen-vl-max",
            messages=[{
                "role": "user",
                "content": [
                    {"type": "text",
                     "text": "这是一张教室黑板照片。请提取所有数学公式（用 LaTeX 表示），并描述其中的几何图形结构。"},
                    {"type": "image_url",
                     "image_url": {"url": f"data:image/jpeg;base64,{b64}"}},
                ],
            }],
            timeout=30,
        )
        return resp.choices[0].message.content
    except Exception as e:
        print(f"[VLM 错误] {e}")
        return f"(多模态解析失败: {e})"


def do_ocr(image_bytes: bytes) -> dict:
    arr = np.frombuffer(image_bytes, np.uint8)
    img = cv2.imdecode(arr, cv2.IMREAD_COLOR)
    if img is None:
        return {"error": "不是有效的图片"}

    saved = save_file(image_bytes, ".jpg")

    # 方案 A：本地 RapidOCR，离线秒出
    ocr = get_ocr()
    result, _ = ocr(img)
    text = "\n".join(line[1] for line in result) if result else ""
    print(f"[OCR] 提取 {len(result) if result else 0} 行文字")

    # 方案 B：多模态大模型解析图形 / 公式（可选）
    ai_analysis = run_vlm_analysis(image_bytes)

    return {
        "status": "success",
        "ocr_text": text,
        "ocr_result": text,            # 兼容旧字段名
        "ai_graphics_analysis": ai_analysis,
        "saved": saved,
    }


def do_asr(audio_bytes: bytes) -> dict:
    if len(audio_bytes) < 1000:
        return {"error": "音频太短"}

    saved = save_file(audio_bytes, ".wav")
    model = get_asr()
    segments, info = model.transcribe(
        saved,
        language="zh",
        beam_size=5,
        initial_prompt="以下是普通话的句子，请用简体中文输出。",
    )
    text = "".join(seg.text for seg in segments).strip()
    print(f"[ASR] {info.duration:.1f}s -> {text}")
    return {
        "status": "success",
        "asr_result": text,
        "language": info.language,
        "duration": round(info.duration, 2),
        "saved": saved,
    }


# ------------------------------------------------------------------
# 路由
# ------------------------------------------------------------------
@app.route("/")
def index():
    return "ESP32 AI Backend (RapidOCR + faster-whisper) is running!"


@app.route("/health")
def health():
    return jsonify({
        "status": "ok",
        "asr_model": ASR_MODEL_NAME,
        "vlm_enabled": bool(DASHSCOPE_API_KEY),
    })


@app.route("/upload", methods=["POST"])
def upload_dispatch():
    """ESP32 主程序用的统一入口，靠 X-Data-Type 区分"""
    kind = request.headers.get("X-Data-Type", "").lower()
    data = read_raw_bytes()
    if not data:
        return jsonify({"error": "没收到数据"}), 400
    try:
        if kind in ("jpeg", "jpg", "image"):
            return jsonify(do_ocr(data))
        if kind in ("wav", "audio", "pcm"):
            return jsonify(do_asr(data))
        return jsonify({"error": f"未知类型: {kind}"}), 400
    except Exception as e:
        print(f"[错误] {e}")
        return jsonify({"error": str(e)}), 500


@app.route("/upload_image", methods=["POST"])
def upload_image():
    data = read_raw_bytes()
    if not data:
        return jsonify({"error": "No image data received"}), 400
    try:
        return jsonify(do_ocr(data))
    except Exception as e:
        print(f"[OCR 错误] {e}")
        return jsonify({"error": str(e)}), 500


@app.route("/upload_audio", methods=["POST"])
def upload_audio():
    data = read_raw_bytes()
    if not data:
        return jsonify({"error": "No audio data received"}), 400
    try:
        return jsonify(do_asr(data))
    except Exception as e:
        print(f"[ASR 错误] {e}")
        return jsonify({"error": str(e)}), 500


if __name__ == "__main__":
    print("=" * 62)
    print("  AI 后端启动中 ... 端口 5000")
    print(f"  ASR 模型 : {ASR_MODEL_NAME}")
    print(f"  多模态   : {'已启用' if DASHSCOPE_API_KEY else '未配置 DASHSCOPE_API_KEY（跳过）'}")
    print("  ESP32 地址: http://<本机IP>:5000/upload")
    print("  查本机 IP: ipconfig  ->  IPv4 地址")
    print("=" * 62)
    app.run(host="0.0.0.0", port=5000, debug=False, threaded=True)
