#pragma once

#ifdef __cplusplus
extern "C" {
#endif

#include "esp_err.h"

// Initialize WiFi STA and wait until we get an IPv4 address or time out.
// Auto-reconnects on disconnect (handled in event_handler).
esp_err_t wifi_app_connect_blocking(const char *ssid, const char *password);

#ifdef __cplusplus
}
#endif
