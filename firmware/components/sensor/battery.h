#pragma once

#ifdef __cplusplus
extern "C" {
#endif

#include "esp_err.h"

// ESP32-S3-RLCD-4.2: BAT_ADC is wired to GPIO4 through a 200k/100k divider.
esp_err_t battery_init(void);
esp_err_t battery_read(float *voltage_v, int *percent);

#ifdef __cplusplus
}
#endif
