#include "battery.h"

#include "esp_adc/adc_cali.h"
#include "esp_adc/adc_cali_scheme.h"
#include "esp_adc/adc_oneshot.h"
#include "esp_log.h"
#include "driver/usb_serial_jtag.h"

#include <stdbool.h>

#define BAT_ADC_CHANNEL ADC_CHANNEL_3
#define BAT_ADC_ATTEN ADC_ATTEN_DB_12
#define BAT_ADC_BITWIDTH ADC_BITWIDTH_DEFAULT
#define BAT_DIVIDER_RATIO 3.0f
#define BAT_SAMPLE_COUNT 16
#define BAT_FALLBACK_FULL_SCALE_MV 3300
#define BAT_PRESENT_MIN_VOLTAGE 3.15f
#define BAT_VALID_MAX_VOLTAGE 4.35f

static const char *TAG = "battery";
static adc_oneshot_unit_handle_t s_adc;
static adc_cali_handle_t s_cali;
static bool s_calibrated;
static bool s_ready;

static int battery_percent_from_voltage(float voltage)
{
    static const struct {
        float voltage;
        int percent;
    } curve[] = {
        {4.20f, 100},
        {4.10f, 90},
        {4.00f, 80},
        {3.92f, 70},
        {3.85f, 60},
        {3.79f, 50},
        {3.73f, 40},
        {3.68f, 30},
        {3.60f, 20},
        {3.50f, 10},
        {3.30f, 0},
    };
    if (voltage >= curve[0].voltage) return 100;
    const int last = (int)(sizeof(curve) / sizeof(curve[0])) - 1;
    if (voltage <= curve[last].voltage) return 0;
    for (int i = 0; i < last; ++i) {
        if (voltage <= curve[i].voltage && voltage >= curve[i + 1].voltage) {
            const float hi_v = curve[i].voltage;
            const float lo_v = curve[i + 1].voltage;
            const int hi_p = curve[i].percent;
            const int lo_p = curve[i + 1].percent;
            const float t = (voltage - lo_v) / (hi_v - lo_v);
            return lo_p + (int)((hi_p - lo_p) * t + 0.5f);
        }
    }
    return 0;
}

esp_err_t battery_init(void)
{
    if (s_ready) return ESP_OK;

    adc_oneshot_unit_init_cfg_t unit_cfg = {
        .unit_id = ADC_UNIT_1,
        .ulp_mode = ADC_ULP_MODE_DISABLE,
    };
    esp_err_t err = adc_oneshot_new_unit(&unit_cfg, &s_adc);
    if (err != ESP_OK) return err;

    adc_oneshot_chan_cfg_t chan_cfg = {
        .atten = BAT_ADC_ATTEN,
        .bitwidth = BAT_ADC_BITWIDTH,
    };
    err = adc_oneshot_config_channel(s_adc, BAT_ADC_CHANNEL, &chan_cfg);
    if (err != ESP_OK) return err;

#if ADC_CALI_SCHEME_CURVE_FITTING_SUPPORTED
    adc_cali_curve_fitting_config_t cali_cfg = {
        .unit_id = ADC_UNIT_1,
        .chan = BAT_ADC_CHANNEL,
        .atten = BAT_ADC_ATTEN,
        .bitwidth = BAT_ADC_BITWIDTH,
    };
    s_calibrated = (adc_cali_create_scheme_curve_fitting(&cali_cfg, &s_cali) == ESP_OK);
#endif
    ESP_LOGI(TAG, "battery ADC ready on GPIO4, calibrated=%d", s_calibrated ? 1 : 0);
    s_ready = true;
    return ESP_OK;
}

esp_err_t battery_read_status(battery_status_t *status)
{
    if (!s_ready) {
        esp_err_t err = battery_init();
        if (err != ESP_OK) return err;
    }

    int raw_sum = 0;
    int mv_sum = 0;
    int samples = 0;
    for (int i = 0; i < BAT_SAMPLE_COUNT; ++i) {
        int raw = 0;
        esp_err_t err = adc_oneshot_read(s_adc, BAT_ADC_CHANNEL, &raw);
        if (err != ESP_OK) continue;
        int mv = (raw * BAT_FALLBACK_FULL_SCALE_MV) / 4095;
        if (s_calibrated) {
            int calibrated_mv = 0;
            if (adc_cali_raw_to_voltage(s_cali, raw, &calibrated_mv) == ESP_OK) {
                mv = calibrated_mv;
            }
        }
        raw_sum += raw;
        mv_sum += mv;
        samples++;
    }
    if (samples == 0) return ESP_FAIL;

    (void)raw_sum;
    float voltage = (mv_sum / (float)samples) * BAT_DIVIDER_RATIO / 1000.0f;
    int pct = battery_percent_from_voltage(voltage);
    battery_power_t power = BATTERY_POWER_BATTERY;
    if (usb_serial_jtag_is_connected() ||
        voltage < BAT_PRESENT_MIN_VOLTAGE ||
        voltage > BAT_VALID_MAX_VOLTAGE) {
        power = BATTERY_POWER_TYPEC;
    }
    if (status) {
        status->voltage_v = voltage;
        status->percent = pct;
        status->power = power;
    }
    ESP_LOGD(TAG, "battery raw=%d mv=%d v=%.3f pct=%d power=%d",
             raw_sum / samples, mv_sum / samples, voltage, pct, (int)power);
    return ESP_OK;
}
