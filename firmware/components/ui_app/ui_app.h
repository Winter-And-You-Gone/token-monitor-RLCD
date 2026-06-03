#pragma once
#ifdef __cplusplus
extern "C" {
#endif

#include "usage_client.h"

void ui_app_init(void);                       // build screen (hold Lvgl_lock)
void ui_app_update(const usage_report_t *r);  // data from bridge (hold lock)
void ui_app_update_pet(const usage_pet_t *pet); // pet state only (hold lock)
void ui_app_set_env(float temp_c, float humidity, bool ok);  // SHTC3 (hold lock)
void ui_app_set_battery(float voltage_v, int percent, bool ok); // Li-ion battery (hold lock)
void ui_app_set_time(const char *hm);         // "14:30" (hold lock)
void ui_app_mark_stale(void);                 // bridge unreachable (hold lock)
void ui_app_toggle_pet_page(void);            // toggle full-screen pet page (hold lock)

#ifdef __cplusplus
}
#endif
