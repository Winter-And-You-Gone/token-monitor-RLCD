idf_component_register(
    SRCS "ui_app.cpp" "icons.c" "pet_anim.c" "pet_big_anim.c" "font_amt14.c" "font_bal28.c" "font_cn14.c"
    INCLUDE_DIRS "./"
    REQUIRES lvgl__lvgl usage_client
    PRIV_REQUIRES app_bsp
)

# lv_font_conv output uses #include "lvgl.h" only under this define
target_compile_definitions(${COMPONENT_LIB} PRIVATE LV_LVGL_H_INCLUDE_SIMPLE)
