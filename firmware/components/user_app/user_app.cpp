#include <stdio.h>
#include <string.h>
#include <stdlib.h>
#include <freertos/FreeRTOS.h>
#include <freertos/task.h>
#include <esp_log.h>
#include <driver/gpio.h>

#include "user_app.h"
#include "wifi_app.h"
#include "ntp.h"
#include "shtc3.h"
#include "battery.h"
#include "usage_client.h"
#include "ui_app.h"
#include "lvgl_bsp.h"

static const char *TAG = "user_app";
static const int USAGE_RETRY_SEC = 10;
static const gpio_num_t PET_PAGE_KEY_GPIO = GPIO_NUM_0;
static const int KEY_SCAN_MS = 20;
static const int KEY_DEBOUNCE_MS = 60;

typedef struct {
    char url[256];
    char token[128];
    int  poll_sec;
    char pet_url[256];
} poll_cfg_t;

// Clock + indoor sensor: cheap, update every 10s independent of the HTTP poll.
static void clock_task(void *arg)
{
    (void) arg;
    for (;;) {
        char hm[8];
        ntp_now_hm(hm, sizeof(hm));
        float t = 0, h = 0;
        bool ok = (shtc3_read(&t, &h) == ESP_OK);
        float bat_v = 0;
        int bat_pct = 0;
        bool bat_ok = (battery_read(&bat_v, &bat_pct) == ESP_OK);
        if (Lvgl_lock(-1)) {
            ui_app_set_time(hm);
            ui_app_set_env(t, h, ok);
            ui_app_set_battery(bat_v, bat_pct, bat_ok);
            Lvgl_unlock();
        }
        vTaskDelay(pdMS_TO_TICKS(10000));
    }
}

static void usage_poll_task(void *arg)
{
    poll_cfg_t *cfg = (poll_cfg_t *) arg;
    for (;;) {
        usage_report_t rep;
        esp_err_t err = usage_client_fetch(cfg->url, cfg->token, &rep);
        if (err == ESP_OK) {
            char hm[8];
            ntp_now_hm(hm, sizeof(hm));
            strncpy(rep.updated_at, hm, sizeof(rep.updated_at) - 1);
            ESP_LOGI(TAG, "usage fetched; next poll in %ds", cfg->poll_sec);
        }
        if (Lvgl_lock(-1)) {
            if (err == ESP_OK) ui_app_update(&rep);
            else { ESP_LOGW(TAG, "fetch failed: %s", esp_err_to_name(err)); ui_app_mark_stale(); }
            Lvgl_unlock();
        }
        int delay_sec = (err == ESP_OK) ? cfg->poll_sec : USAGE_RETRY_SEC;
        vTaskDelay(pdMS_TO_TICKS(delay_sec * 1000));
    }
}


static void pet_poll_task(void *arg)
{
    poll_cfg_t *cfg = (poll_cfg_t *) arg;
    char last_pet_update[40] = "";
    for (;;) {
        usage_pet_t pet;
        char pet_update[40] = "";
        esp_err_t err = usage_client_fetch_pet(cfg->pet_url, cfg->token, last_pet_update, pet_update, sizeof(pet_update), &pet);
        if (err == ESP_OK) {
            if (pet_update[0]) strncpy(last_pet_update, pet_update, sizeof(last_pet_update) - 1);
            if (Lvgl_lock(-1)) {
                ui_app_update_pet(&pet);
                Lvgl_unlock();
            }
        } else {
            vTaskDelay(pdMS_TO_TICKS(1000));
        }
    }
}

static void pet_page_key_task(void *arg)
{
    (void) arg;
    gpio_config_t io_conf = {};
    io_conf.intr_type = GPIO_INTR_DISABLE;
    io_conf.mode = GPIO_MODE_INPUT;
    io_conf.pin_bit_mask = (1ULL << PET_PAGE_KEY_GPIO);
    io_conf.pull_down_en = GPIO_PULLDOWN_DISABLE;
    io_conf.pull_up_en = GPIO_PULLUP_ENABLE;
    ESP_ERROR_CHECK_WITHOUT_ABORT(gpio_config(&io_conf));

    int stable_level = gpio_get_level(PET_PAGE_KEY_GPIO);
    int last_level = stable_level;
    TickType_t last_change = xTaskGetTickCount();

    for (;;) {
        int level = gpio_get_level(PET_PAGE_KEY_GPIO);
        TickType_t now = xTaskGetTickCount();
        if (level != last_level) {
            last_level = level;
            last_change = now;
        }
        if (level != stable_level &&
            (now - last_change) >= pdMS_TO_TICKS(KEY_DEBOUNCE_MS)) {
            stable_level = level;
            if (stable_level == 0) {
                if (Lvgl_lock(pdMS_TO_TICKS(500))) {
                    ui_app_toggle_pet_page();
                    Lvgl_unlock();
                }
            }
        }
        vTaskDelay(pdMS_TO_TICKS(KEY_SCAN_MS));
    }
}

void UserApp_AppInit(const char *ssid, const char *password)
{
    ESP_LOGI(TAG, "connecting to '%s' ...", ssid);
    esp_err_t wifi_err = wifi_app_connect_blocking(ssid, password);
    if (wifi_err != ESP_OK) {
        ESP_LOGW(TAG, "wifi not ready yet: %s", esp_err_to_name(wifi_err));
    }
    ntp_start();
    if (shtc3_init() != ESP_OK) ESP_LOGW(TAG, "shtc3 init failed");
    if (battery_init() != ESP_OK) ESP_LOGW(TAG, "battery init failed");
}

void UserApp_UiInit(void)
{
    ui_app_init();
}

void UserApp_TaskInit(const char *bridge_url, const char *token, int poll_sec)
{
    poll_cfg_t *cfg = (poll_cfg_t *) calloc(1, sizeof(*cfg));
    if (!cfg) {
        ESP_LOGE(TAG, "failed to allocate poll config");
        return;
    }
    strncpy(cfg->url, bridge_url, sizeof(cfg->url) - 1);
    snprintf(cfg->pet_url, sizeof(cfg->pet_url), "%s", bridge_url);
    char *api = strstr(cfg->pet_url, "/api/");
    if (api) snprintf(api, sizeof(cfg->pet_url) - (api - cfg->pet_url), "/api/pet/state");
    if (token) strncpy(cfg->token, token, sizeof(cfg->token) - 1);
    cfg->poll_sec = poll_sec > 0 ? poll_sec : 60;
    BaseType_t poll_ok = xTaskCreatePinnedToCore(usage_poll_task, "usage_poll", 6 * 1024, cfg, 4, NULL, 1);
    if (poll_ok != pdPASS) {
        ESP_LOGE(TAG, "failed to create usage_poll task");
        free(cfg);
        return;
    }
    BaseType_t pet_ok = xTaskCreatePinnedToCore(pet_poll_task, "pet_poll", 4 * 1024, cfg, 3, NULL, 1);
    if (pet_ok != pdPASS) {
        ESP_LOGE(TAG, "failed to create pet_poll task");
    }
    BaseType_t clock_ok = xTaskCreatePinnedToCore(clock_task, "clock", 4 * 1024, NULL, 3, NULL, 1);
    if (clock_ok != pdPASS) {
        ESP_LOGE(TAG, "failed to create clock task");
    }
    BaseType_t key_ok = xTaskCreatePinnedToCore(pet_page_key_task, "pet_key", 2 * 1024, NULL, 3, NULL, 1);
    if (key_ok != pdPASS) {
        ESP_LOGE(TAG, "failed to create pet_key task");
    }
}
