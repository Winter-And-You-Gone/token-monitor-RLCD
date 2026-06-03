idf_component_register(
    SRCS "user_app.cpp"
    INCLUDE_DIRS "./"
    PRIV_REQUIRES net_app usage_client ui_app app_bsp sensor esp_driver_gpio
)
