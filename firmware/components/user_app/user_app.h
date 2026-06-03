#pragma once

#ifdef __cplusplus
extern "C" {
#endif

// wifi connect (waits briefly, then lets background reconnect continue)
void UserApp_AppInit(const char *ssid, const char *password);

// build LVGL screen (must hold Lvgl_lock when called)
void UserApp_UiInit(void);

// start HTTP polling task. token may be NULL/"" for no auth.
void UserApp_TaskInit(const char *bridge_url, const char *token, int poll_sec);

#ifdef __cplusplus
}
#endif
