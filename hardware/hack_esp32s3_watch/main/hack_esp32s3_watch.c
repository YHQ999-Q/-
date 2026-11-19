/**
 * ============================================================
 *  MetaMind · ESP32-S3 学习触发器（按键版）
 *
 *  和上一版最大的区别：
 *    - 不再每 10 秒拍照、每 30 秒录音（那是 Bee 的形态）
 *    - 只在学生按下按键时，才拍一张板书 + 录 5 秒问题
 *    - 一次按键 = 一张图 + 一段音 + 一个 commit，后端才开始分析
 *
 *  按键：
 *    KEY_PHOTO  (GPIO41)  按一下：拍板书 + 说一句 + 上传分析
 *    KEY_RECORD (GPIO42)  按一下：只录 5 秒语音（不上传图片）
 *    VIB_PIN    (GPIO43)  震动马达：短震=开始，两下=成功，长震=失败
 *
 *  编译：ESP-IDF v5.2.8 + esp32-camera
 *  改配置：main/app_config.h（WiFi / 电脑 IP / 设备名）
 *  改引脚：main/board_config.h
 * ============================================================
 */

#include <stdio.h>
#include <string.h>
#include <stdlib.h>
#include <stdbool.h>

#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "freertos/event_groups.h"

#include "esp_log.h"
#include "esp_system.h"
#include "esp_http_client.h"
#include "nvs_flash.h"
#include "driver/gpio.h"

#include "board_config.h"
#include "app_config.h"

#if ENABLE_WIFI
#include "esp_wifi.h"
#include "esp_event.h"
#include "esp_netif.h"
#endif

#if ENABLE_MIC
#include "driver/i2s_std.h"
#endif

#if ENABLE_CAMERA
#include "esp_camera.h"
#endif

static const char *TAG = "METAMIND";

/* ============================================================
 * 1. WiFi
 * ============================================================ */
#if ENABLE_WIFI
static EventGroupHandle_t s_wifi_event_group;
#define WIFI_CONNECTED_BIT BIT0

static void wifi_event_handler(void *arg, esp_event_base_t base,
                               int32_t event_id, void *event_data)
{
    if (base == WIFI_EVENT && event_id == WIFI_EVENT_STA_START) {
        esp_wifi_connect();
    } else if (base == WIFI_EVENT && event_id == WIFI_EVENT_STA_DISCONNECTED) {
        /* Never block the WiFi event task here: a 2s delay stalls reconnects. */
        if (s_wifi_event_group) {
            xEventGroupClearBits(s_wifi_event_group, WIFI_CONNECTED_BIT);
        }
        ESP_LOGW(TAG, "WiFi 断开，立即重连...");
        esp_wifi_connect();
    } else if (base == IP_EVENT && event_id == IP_EVENT_STA_GOT_IP) {
        ip_event_got_ip_t *ev = (ip_event_got_ip_t *)event_data;
        ESP_LOGI(TAG, "拿到 IP: " IPSTR, IP2STR(&ev->ip_info.ip));
        xEventGroupSetBits(s_wifi_event_group, WIFI_CONNECTED_BIT);
    }
}

static void wifi_init_sta(void)
{
    s_wifi_event_group = xEventGroupCreate();
    ESP_ERROR_CHECK(esp_netif_init());
    ESP_ERROR_CHECK(esp_event_loop_create_default());
    esp_netif_create_default_wifi_sta();

    wifi_init_config_t cfg = WIFI_INIT_CONFIG_DEFAULT();
    ESP_ERROR_CHECK(esp_wifi_init(&cfg));
    ESP_ERROR_CHECK(esp_event_handler_instance_register(WIFI_EVENT, ESP_EVENT_ANY_ID,
                                                        &wifi_event_handler, NULL, NULL));
    ESP_ERROR_CHECK(esp_event_handler_instance_register(IP_EVENT, IP_EVENT_STA_GOT_IP,
                                                        &wifi_event_handler, NULL, NULL));

    wifi_config_t wifi_config = { 0 };
    strncpy((char *)wifi_config.sta.ssid, WIFI_SSID, sizeof(wifi_config.sta.ssid) - 1);
    strncpy((char *)wifi_config.sta.password, WIFI_PASS, sizeof(wifi_config.sta.password) - 1);
    wifi_config.sta.threshold.authmode = WIFI_AUTH_WPA2_PSK;

    ESP_ERROR_CHECK(esp_wifi_set_mode(WIFI_MODE_STA));
    ESP_ERROR_CHECK(esp_wifi_set_config(WIFI_IF_STA, &wifi_config));
    ESP_ERROR_CHECK(esp_wifi_start());
    esp_wifi_set_ps(WIFI_PS_NONE);   /* MiFi 热点会把省电客户端踢掉 */
    ESP_LOGI(TAG, "正在连接 WiFi: %s", WIFI_SSID);
}

static bool wifi_wait(int timeout_ms)
{
    if (s_wifi_event_group == NULL) return false;
    EventBits_t bits = xEventGroupWaitBits(s_wifi_event_group, WIFI_CONNECTED_BIT,
                                           pdFALSE, pdFALSE, pdMS_TO_TICKS(timeout_ms));
    return (bits & WIFI_CONNECTED_BIT) != 0;
}
#endif

/* ============================================================
 * 2. HTTP 上传（原始字节，C 端不需要拼 multipart）
 * ============================================================ */
static esp_err_t http_post_bytes(const char *url, const uint8_t *data, size_t len)
{
    esp_http_client_config_t cfg = {
        .url = url,
        .method = HTTP_METHOD_POST,
        .timeout_ms = 30000,
    };
    esp_http_client_handle_t client = esp_http_client_init(&cfg);
    if (client == NULL) return ESP_FAIL;

    esp_http_client_set_header(client, "Content-Type", "application/octet-stream");
    esp_http_client_set_post_field(client, (const char *)data, (int)len);

    esp_err_t err = esp_http_client_perform(client);
    int status = esp_http_client_get_status_code(client);
    if (err == ESP_OK && status >= 200 && status < 300) {
        ESP_LOGI(TAG, "上传成功 %u 字节 HTTP %d", (unsigned)len, status);
    } else {
        ESP_LOGE(TAG, "上传失败 %u 字节 HTTP %d (%s)", (unsigned)len, status,
                 esp_err_to_name(err));
        if (err == ESP_OK) err = ESP_FAIL;
    }
    esp_http_client_cleanup(client);
    return err;
}

static esp_err_t http_post_empty(const char *url)
{
    return http_post_bytes(url, (const uint8_t *)"", 0);
}

/* ============================================================
 * 3. 摄像头
 * ============================================================ */
#if ENABLE_CAMERA
static esp_err_t camera_start(framesize_t fs, int fb_count)
{
    camera_config_t config = {
        .pin_pwdn     = CAM_PIN_PWDN,
        .pin_reset    = CAM_PIN_RESET,
        .pin_xclk     = CAM_PIN_XCLK,
        .pin_sccb_sda = CAM_PIN_SIOD,
        .pin_sccb_scl = CAM_PIN_SIOC,
        .pin_d7       = CAM_PIN_D7,
        .pin_d6       = CAM_PIN_D6,
        .pin_d5       = CAM_PIN_D5,
        .pin_d4       = CAM_PIN_D4,
        .pin_d3       = CAM_PIN_D3,
        .pin_d2       = CAM_PIN_D2,
        .pin_d1       = CAM_PIN_D1,
        .pin_d0       = CAM_PIN_D0,
        .pin_vsync    = CAM_PIN_VSYNC,
        .pin_href     = CAM_PIN_HREF,
        .pin_pclk     = CAM_PIN_PCLK,

        .xclk_freq_hz = 20000000,
        .ledc_timer   = LEDC_TIMER_0,
        .ledc_channel = LEDC_CHANNEL_0,

        .pixel_format = PIXFORMAT_JPEG,
        .frame_size   = fs,
        .jpeg_quality = 10,                /* 0-63，数字越小越清晰 */
        .fb_count     = fb_count,
        .fb_location  = CAMERA_FB_IN_DRAM,
        .grab_mode    = CAMERA_GRAB_WHEN_EMPTY,
    };
    return esp_camera_init(&config);
}

static esp_err_t camera_init(void)
{
    /* 先试 VGA（板书字清楚）；没 PSRAM 的板子可能内存不够，退回 QVGA。 */
    esp_err_t err = camera_start(FRAMESIZE_VGA, 1);
    if (err != ESP_OK) {
        ESP_LOGW(TAG, "VGA 初始化失败 (0x%x)，退回 QVGA", err);
        err = camera_start(FRAMESIZE_QVGA, 1);
    }
    if (err != ESP_OK) {
        ESP_LOGE(TAG, "摄像头初始化失败 (0x%x) —— 检查排线/引脚/供电", err);
        return err;
    }
    ESP_LOGI(TAG, "摄像头 OK");
    return ESP_OK;
}

static esp_err_t upload_photo(void)
{
    camera_fb_t *fb = esp_camera_fb_get();
    if (fb == NULL) {
        ESP_LOGE(TAG, "取帧失败（摄像头没插好或引脚不对）");
        return ESP_FAIL;
    }
    char url[192];
    snprintf(url, sizeof(url), "%s/api/teach/raw?device_id=%s&kind=image",
             SERVER_BASE, DEVICE_ID);
    ESP_LOGI(TAG, "拍照 %ux%u, %u 字节", fb->width, fb->height, (unsigned)fb->len);
    esp_err_t err = http_post_bytes(url, fb->buf, fb->len);
    esp_camera_fb_return(fb);
    return err;
}
#endif /* ENABLE_CAMERA */

/* ============================================================
 * 4. 麦克风
 * ============================================================ */
#if ENABLE_MIC
static i2s_chan_handle_t s_rx_handle = NULL;

static esp_err_t mic_init(void)
{
    i2s_chan_config_t chan_cfg = I2S_CHANNEL_DEFAULT_CONFIG(MIC_I2S_PORT, I2S_ROLE_MASTER);
    chan_cfg.auto_clear = true;

    esp_err_t err = i2s_new_channel(&chan_cfg, NULL, &s_rx_handle);
    if (err != ESP_OK) {
        ESP_LOGE(TAG, "创建 I2S 通道失败 (0x%x)", err);
        return err;
    }

    i2s_std_config_t std_cfg = {
        .clk_cfg  = I2S_STD_CLK_DEFAULT_CONFIG(MIC_SAMPLE_RATE),
        /* INMP441 outputs 24-bit data in 32-bit slots; 16-bit slots often read zeros. */
        .slot_cfg = I2S_STD_PHILIPS_SLOT_DEFAULT_CONFIG(I2S_DATA_BIT_WIDTH_32BIT,
                                                        I2S_SLOT_MODE_STEREO),
        .gpio_cfg = {
            .mclk = I2S_GPIO_UNUSED,
            .bclk = MIC_PIN_BCLK,
            .ws   = MIC_PIN_WS,
            .dout = I2S_GPIO_UNUSED,
            .din  = MIC_PIN_DIN,
            .invert_flags = { .mclk_inv = false, .bclk_inv = false, .ws_inv = false },
        },
    };
    std_cfg.slot_cfg.slot_mask = I2S_STD_SLOT_BOTH;   /* 双麦：左右两个声道都收 */

    err = i2s_channel_init_std_mode(s_rx_handle, &std_cfg);
    if (err != ESP_OK) {
        ESP_LOGE(TAG, "I2S 标准模式初始化失败 (0x%x)", err);
        return err;
    }
    err = i2s_channel_enable(s_rx_handle);
    if (err != ESP_OK) {
        ESP_LOGE(TAG, "I2S 通道使能失败 (0x%x)", err);
        return err;
    }
    ESP_LOGI(TAG, "麦克风 OK (%d Hz, 双麦立体声 → 单声道输出)", MIC_SAMPLE_RATE);
    return ESP_OK;
}

/* ------------------------------------------------------------------
 * 双麦：两颗 INMP441 共用 BCLK / WS / SD，靠各自的 L/R 脚分左右声道。
 *   L/R 接 GND → 左声道（建议放你这边，近场）
 *   L/R 接 3V3 → 右声道（建议朝讲台，远场）
 * 每 1024 帧（约 64ms）比较两路能量，取更近的那一路，输出单声道。
 * 带 1.5 倍滞回，避免两路音量接近时来回切（会有"咔哒"感）。
 * 这既是降噪，也是"谁在说话"的粗略判据。
 * ---------------------------------------------------------------- */
#define MIC_FRAMES_PER_BLOCK 1024
static int32_t s_stereo[MIC_FRAMES_PER_BLOCK * 2];
static int s_last_left = 1;

static size_t mic_record(uint8_t *buf, size_t buf_size)
{
    if (s_rx_handle == NULL) return 0;

    size_t frames_wanted = buf_size / 2;     /* 输出是 16bit 单声道 */
    size_t done = 0;
    int16_t *out = (int16_t *)buf;

    while (done < frames_wanted) {
        size_t frames = frames_wanted - done;
        if (frames > MIC_FRAMES_PER_BLOCK) frames = MIC_FRAMES_PER_BLOCK;

        size_t want = frames * 8;            /* 2 声道 × 4 字节（32-bit 槽） */
        size_t got = 0;
        esp_err_t err = i2s_channel_read(s_rx_handle, s_stereo, want, &got,
                                         pdMS_TO_TICKS(2000));
        if (err != ESP_OK) {
            ESP_LOGE(TAG, "I2S 读取失败: %s", esp_err_to_name(err));
            break;
        }
        size_t n = got / 8;
        if (n == 0) break;

        int64_t eL = 0, eR = 0;
        for (size_t i = 0; i < n; i++) {
            int32_t l = s_stereo[i * 2] >> 16;
            int32_t r = s_stereo[i * 2 + 1] >> 16;
            eL += (l < 0 ? -l : l);
            eR += (r < 0 ? -r : r);
        }

        int use_left = s_last_left;
        if (s_last_left && eR * 3 > eL * 2)       use_left = 0;  /* 右强 1.5 倍 */
        else if (!s_last_left && eL * 3 > eR * 2) use_left = 1;  /* 左强 1.5 倍 */
        s_last_left = use_left;

        for (size_t i = 0; i < n; i++) {
            int32_t sample = use_left ? s_stereo[i * 2] : s_stereo[i * 2 + 1];
            sample >>= 16;
            sample *= 8;                 /* INMP441 现场音量偏低，先给 8 倍增益 */
            if (sample > 32767)  sample = 32767;
            if (sample < -32768) sample = -32768;
            out[done + i] = (int16_t)sample;
        }
        done += n;
    }

    return done * 2;
}

#define WAV_HEADER_LEN 44

static void wav_fill_header(uint8_t *h, uint32_t pcm_bytes)
{
    uint32_t chunk_size  = 36 + pcm_bytes;
    uint32_t byte_rate   = MIC_SAMPLE_RATE * 2;
    uint32_t fmt_size    = 16;
    uint16_t fmt_type    = 1;
    uint16_t channels    = 1;
    uint32_t rate        = MIC_SAMPLE_RATE;
    uint16_t block_align = 2;
    uint16_t bits        = 16;

    memcpy(h + 0,  "RIFF", 4);
    memcpy(h + 4,  &chunk_size, 4);
    memcpy(h + 8,  "WAVEfmt ", 8);
    memcpy(h + 16, &fmt_size, 4);
    memcpy(h + 20, &fmt_type, 2);
    memcpy(h + 22, &channels, 2);
    memcpy(h + 24, &rate, 4);
    memcpy(h + 28, &byte_rate, 4);
    memcpy(h + 32, &block_align, 2);
    memcpy(h + 34, &bits, 2);
    memcpy(h + 36, "data", 4);
    memcpy(h + 40, &pcm_bytes, 4);
}

static uint8_t *s_audio_buf = NULL;

static esp_err_t upload_audio(void)
{
    if (s_audio_buf == NULL) return ESP_FAIL;
    ESP_LOGI(TAG, "开始录音 %d 秒...", MIC_RECORD_SEC);
    size_t got = mic_record(s_audio_buf + WAV_HEADER_LEN, MIC_BUF_BYTES);
    if (got == 0) return ESP_FAIL;
    wav_fill_header(s_audio_buf, (uint32_t)got);

    char url[192];
    snprintf(url, sizeof(url), "%s/api/teach/raw?device_id=%s&kind=audio",
             SERVER_BASE, DEVICE_ID);
    ESP_LOGI(TAG, "录到 %u 字节音频", (unsigned)got);
    return http_post_bytes(url, s_audio_buf, got + WAV_HEADER_LEN);
}
#endif /* ENABLE_MIC */

/* ============================================================
 * 5. 按键 + 震动
 * ============================================================ */
static void vib(int ms)
{
    gpio_set_level(VIB_PIN, 1);
    vTaskDelay(pdMS_TO_TICKS(ms));
    gpio_set_level(VIB_PIN, 0);
}

static void buttons_init(void)
{
    gpio_config_t in = {
        .pin_bit_mask = (1ULL << KEY_PHOTO) | (1ULL << KEY_RECORD) | (1ULL << KEY_BOOT),
        .mode = GPIO_MODE_INPUT,
        .pull_up_en = GPIO_PULLUP_ENABLE,
        .pull_down_en = GPIO_PULLDOWN_DISABLE,
        .intr_type = GPIO_INTR_DISABLE,
    };
    gpio_config(&in);
    gpio_config_t out = {
        .pin_bit_mask = (1ULL << VIB_PIN),
        .mode = GPIO_MODE_OUTPUT,
        .pull_up_en = GPIO_PULLUP_DISABLE,
        .pull_down_en = GPIO_PULLDOWN_DISABLE,
        .intr_type = GPIO_INTR_DISABLE,
    };
    gpio_config(&out);
    gpio_set_level(VIB_PIN, 0);
}

static esp_err_t commit_job(void)
{
    char url[192];
    snprintf(url, sizeof(url), "%s/api/teach/commit?device_id=%s",
             SERVER_BASE, DEVICE_ID);
    return http_post_empty(url);
}

static void capture_and_ask(void)
{
#if ENABLE_WIFI
    if (!wifi_wait(15000)) {
        ESP_LOGE(TAG, "WiFi 没连上，先检查 SSID/密码/IP");
        vib(600);
        return;
    }
#endif
    ESP_LOGI(TAG, "=== 一次提问开始 ===");
    vib(80);

    bool ok = true;
#if ENABLE_CAMERA
    if (upload_photo() != ESP_OK) ok = false;
#endif
#if ENABLE_MIC
    if (upload_audio() != ESP_OK) ok = false;
#endif
    if (ok) {
        if (commit_job() != ESP_OK) ok = false;
    }

    if (ok) {
        ESP_LOGI(TAG, "=== 已提交，等网页/OLED 出结果 ===");
        vib(120); vTaskDelay(pdMS_TO_TICKS(120)); vib(120);
    } else {
        ESP_LOGE(TAG, "=== 这次没传成功，按一下重试 ===");
        vib(600);
    }
}

static void record_only(void)
{
#if ENABLE_WIFI
    if (!wifi_wait(15000)) { vib(600); return; }
#endif
    vib(80);
#if ENABLE_MIC
    bool ok = upload_audio() == ESP_OK && commit_job() == ESP_OK;
    vib(ok ? 200 : 600);
#endif
}

/* ============================================================
 * 6. 主任务：只轮询按键，不自动采集
 * ============================================================ */
static void watch_task(void *arg)
{
    int last_photo = 1, last_record = 1;
    while (1) {
        int p = gpio_get_level(KEY_PHOTO);
        int r = gpio_get_level(KEY_RECORD);
        int b = gpio_get_level(KEY_BOOT);
        int record_pressed = (r == 0 || b == 0);

        if (last_photo == 1 && p == 0) {
            capture_and_ask();
            vTaskDelay(pdMS_TO_TICKS(250));      /* 防抖 */
        }
        if (last_record == 1 && record_pressed) {
            record_only();
            vTaskDelay(pdMS_TO_TICKS(250));
        }
        last_photo = p;
        last_record = record_pressed ? 0 : 1;
        vTaskDelay(pdMS_TO_TICKS(50));
    }
}

#if AUTO_CAPTURE_ON_BOOT
static void auto_capture_task(void *arg)
{
    vTaskDelay(pdMS_TO_TICKS(6000));      /* 等 WiFi 稳定 */
    ESP_LOGI(TAG, "自动采集：现在请说话（5 秒）");
    vib(300);                             /* 震动提示开始 */
    record_only();
}
#endif

/* ============================================================
 * 7. 入口
 * ============================================================ */
void app_main(void)
{
    ESP_LOGI(TAG, "===== MetaMind 学习触发器启动 =====");
    ESP_LOGI(TAG, "按键才采集：KEY_PHOTO=%d KEY_RECORD=%d", KEY_PHOTO, KEY_RECORD);

    esp_err_t ret = nvs_flash_init();
    if (ret == ESP_ERR_NVS_NO_FREE_PAGES || ret == ESP_ERR_NVS_NEW_VERSION_FOUND) {
        ESP_ERROR_CHECK(nvs_flash_erase());
        ret = nvs_flash_init();
    }
    ESP_ERROR_CHECK(ret);

#if ENABLE_WIFI
    wifi_init_sta();
#endif
#if ENABLE_CAMERA
    camera_init();
#endif
#if ENABLE_MIC
    mic_init();
    s_audio_buf = (uint8_t *)malloc(MIC_BUF_BYTES + WAV_HEADER_LEN);
    if (s_audio_buf == NULL) ESP_LOGE(TAG, "音频缓冲区分配失败");
#endif
    buttons_init();

    xTaskCreate(watch_task, "watch_task", 8192, NULL, 5, NULL);
#if AUTO_CAPTURE_ON_BOOT
    xTaskCreate(auto_capture_task, "auto_capture", 8192, NULL, 4, NULL);
#endif
    ESP_LOGI(TAG, "初始化完成：按 KEY_PHOTO 开始一次提问");
}
