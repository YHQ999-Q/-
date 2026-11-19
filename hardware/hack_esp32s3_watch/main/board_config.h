/**
 * ============================================================
 *  硬件引脚表  board_config.h
 *
 *  规则：现场只改这个文件 + 接线，不动主程序逻辑
 *  接线前先把板子丝印 / 卖家给的引脚图核对一遍
 * ============================================================
 */
#pragma once

/* ------------------------------------------------------------
 * 摄像头 OV5640 / OV2640（S3-CAM）
 * ------------------------------------------------------------ */
/* ESP32-S3 N16R8 / Freenove ESP32-S3 WROOM CAM pinout */
#define CAM_PIN_PWDN    -1
#define CAM_PIN_RESET   -1
#define CAM_PIN_XCLK    15
#define CAM_PIN_SIOD    4       /* SCCB SDA */
#define CAM_PIN_SIOC    5       /* SCCB SCL */
#define CAM_PIN_D7      16
#define CAM_PIN_D6      17
#define CAM_PIN_D5      18
#define CAM_PIN_D4      12
#define CAM_PIN_D3      10
#define CAM_PIN_D2      8
#define CAM_PIN_D1      9
#define CAM_PIN_D0      11
#define CAM_PIN_VSYNC   6
#define CAM_PIN_HREF    7
#define CAM_PIN_PCLK    13

/* ------------------------------------------------------------
 * I2S 麦克风 INMP441
 * ------------------------------------------------------------ */
#define MIC_I2S_PORT    I2S_NUM_0
#define MIC_PIN_WS      21      /* LRCK / WS */
#define MIC_PIN_SCK     20      /* BCLK / SCK */
#define MIC_PIN_SD      19      /* DIN / SD */

/* ---- 双麦接法（两颗 INMP441 共用这三根线）----
 *   VDD  → 3.3V      GND → GND
 *   SCK  → GPIO20    WS  → GPIO21    SD → GPIO19（两颗都接同一根 SD）
 *   L/R 脚决定声道：接 GND = 左声道（放你这边）
 *                   接 3V3 = 右声道（朝讲台）
 * 只焊一颗也能跑：另一路静音，固件会自动选有声的那一路。
 */

/* 主程序里用的旧名字，统一映射过来 */
#define MIC_PIN_BCLK    MIC_PIN_SCK
#define MIC_PIN_DIN     MIC_PIN_SD

/* ------------------------------------------------------------
 * 按键 / 振动传感器
 * ------------------------------------------------------------ */
#define KEY_PHOTO       41
#define KEY_RECORD      42
#define KEY_BOOT        0       /* 板上 BOOT 键：没有外接按键时用它只录音 */
/* 注意：43/44 是 UART0 的 TX/RX（串口日志），绝不能当 GPIO 用，
 * 一配成输出日志就断，看起来像"反复重启"。振动脚改到 47。 */
#define VIB_PIN         2

/* ============================================================
 * 功能开关（降级方案）
 *   某个外设现场坏了 / 线断了，把它改成 0 重新编译，
 *   其余功能照常演示。比赛现场救命用。
 * ============================================================ */
#define ENABLE_CAMERA   0       /* 1=启用摄像头，0=关掉 */
#define ENABLE_MIC      1       /* 1=启用麦克风录音，0=关掉 */
