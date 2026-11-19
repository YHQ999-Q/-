/**
 * ============================================================
 *  应用配置  app_config.h
 *
 *  比赛现场只改这三处：WiFi、电脑 IP、设备名。
 *  电脑和手表必须连同一个 2.4G WiFi。
 * ============================================================
 */
#pragma once

/* ---- WiFi：必须是 2.4G，SSID 不能有中文 ---- */
#define WIFI_SSID       "MiFi-0ED7"
#define WIFI_PASS       "1234567890"

/* ---- 电脑上 MetaMind server 的地址 ----
 * 查 IP：命令行输入 ipconfig，看「IPv4 地址」。
 * 端口 5001 是 MetaMind 教学服务；5000 是 OCR/ASR 服务，不用直接连。
 */
#define SERVER_BASE     "http://192.168.1.195:5001"
#define DEVICE_ID       "esp32-s3-A1B2"

/* ---- 功能总开关 ---- */
#define ENABLE_WIFI     1       /* 0 = 不连 WiFi（纯离线调试用） */
#define AUTO_CAPTURE_ON_BOOT 1  /* 演示模式：上电/按 RST 后自动录一次 5 秒 */

/* ---- 采样参数 ---- */
#define MIC_SAMPLE_RATE 16000                   /* 16 kHz，语音够用 */
#define MIC_RECORD_SEC  5                       /* 一次提问录 5 秒 */
#define MIC_BUF_BYTES   (MIC_SAMPLE_RATE * 2 * MIC_RECORD_SEC)

/* ---- 旧版定时采集已删除 ----
 * 现在只在你按下按键时拍一张、录一段。
 * 这是和 Bee 那种"全天候环境录音"最本质的区别：不按键，不采集。
 */
