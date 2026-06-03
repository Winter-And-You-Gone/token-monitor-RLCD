#pragma once

#include "pet_anim.h"

#ifdef __cplusplus
extern "C" {
#endif

const pet_anim_sequence_t *ui_pet_big_anim_for_asset(const char *asset);
const pet_anim_sequence_t *ui_pet_big_anim_for_state(const char *state);
const pet_anim_sequence_t *ui_pet_big_anim_idle(void);

#ifdef __cplusplus
}
#endif
