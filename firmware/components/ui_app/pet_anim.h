#pragma once

#include <stdint.h>
#include "lvgl.h"

#ifdef __cplusplus
extern "C" {
#endif

typedef struct {
    const lv_image_dsc_t *const *frames;
    const lv_image_dsc_t *const *eye_frames;
    const uint16_t *durations_ms;
    uint16_t frame_count;
    uint16_t width;
    uint16_t height;
} pet_anim_sequence_t;

const pet_anim_sequence_t *ui_pet_anim_for_asset(const char *asset);
const pet_anim_sequence_t *ui_pet_anim_for_state(const char *state);
const pet_anim_sequence_t *ui_pet_anim_idle(void);

#ifdef __cplusplus
}
#endif
