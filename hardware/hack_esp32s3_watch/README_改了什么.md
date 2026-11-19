# MetaMind 固件改动说明（按键触发版）

## 一句话

上一版每 10 秒拍照、每 30 秒录音，等于一个随身环境记录器（这就是像 Bee 的原因）。
这一版**只在学生按键时采集一次**：一张板书 + 5 秒提问，上传后后端才开始分析。

## 改了哪几个文件

| 文件 | 改了什么 |
|---|---|
| `main/app_config.h` | 新增 `SERVER_BASE`（电脑 IP:5001）、`DEVICE_ID`；录音改成 5 秒；删掉定时采集宏 |
| `main/hack_esp32s3_watch.c` | 删除定时拍照/录音循环；新增按键轮询、按钮触发、震动反馈、裸字节上传 |
| `main/board_config.h` | 没动（引脚表保持原样） |

## 三个接口

手表按一次 `KEY_PHOTO`（GPIO41）会依次请求：

```
POST {SERVER_BASE}/api/teach/raw?device_id={DEVICE_ID}&kind=image   JPEG 原始字节
POST {SERVER_BASE}/api/teach/raw?device_id={DEVICE_ID}&kind=audio   WAV 原始字节
POST {SERVER_BASE}/api/teach/commit?device_id={DEVICE_ID}           开始分析
```

后端凑齐图 + 音后跑完整链路：RapidOCR / Qwen-VL → Faster-Whisper / SenseVoice →
裁决 → 3 道自测 → 计划更新。

`KEY_RECORD`（GPIO42）只上传音频，适合"我口述一道题"的场景。

## 烧录前只改两行

打开 `main/app_config.h`：

```c
#define WIFI_SSID   "你的 2.4G WiFi 名"
#define WIFI_PASS   "你的 WiFi 密码"
#define SERVER_BASE "http://192.168.1.199:5001"   // 改成运行 server.py 那台电脑的 IPv4
```

查 IP：`ipconfig` 看「IPv4 地址」。电脑和手表必须同一个 WiFi。

## 编译

用桌面的 `ESP-IDF 5.2 CMD` 打开，进入本目录：

```bat
idf.py set-target esp32s3
idf.py build
idf.py -p COM口 flash monitor
```

如果摄像头/麦克风现场坏了：

- 关摄像头：`board_config.h` 里 `ENABLE_CAMERA 0`
- 关麦克风：`board_config.h` 里 `ENABLE_MIC 0`
- 只测按键和 WiFi：两个都改成 0，按 `KEY_PHOTO` 仍会请求 commit（后端会返回 nothing uploaded，但串口能看到按键触发）

## 还没做（今天不做，别在答辩里吹）

- OLED 显示：后端已经留了 `GET /api/teach/result/{device_id}`，返回一行 <=21 字符的状态；
  接 SSD1306 后轮询它即可。
- 断网缓存：现在 WiFi 没连上会震动 600ms 报错，不会缓存到 flash。
- 电池管理：按键触发本身已经比定时采集省电，但没有做 deep sleep。
