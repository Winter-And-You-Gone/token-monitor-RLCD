#pragma once

#ifdef __cplusplus
extern "C" {
#endif

#include "esp_err.h"

// ESP32-S3-RLCD-4.2: BAT_ADC is wired to GPIO4 through a 200k/100k divider.
typedef enum {
    BATTERY_POWER_UNKNOWN = 0,
    BATTERY_POWER_BATTERY,
    BATTERY_POWER_TYPEC,
} battery_power_t;

typedef struct {
    float voltage_v;
    int percent;
    battery_power_t power;
} battery_status_t;

esp_err_t battery_init(void);
esp_err_t battery_read_status(battery_status_t *status);

#ifdef __cplusplus
}
#endif
