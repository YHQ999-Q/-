idf_component_register(SRCS "hack_esp32s3_watch.c"
                    INCLUDE_DIRS "."
                    REQUIRES esp32-camera esp_http_client driver esp_wifi nvs_flash esp_event esp_netif)
