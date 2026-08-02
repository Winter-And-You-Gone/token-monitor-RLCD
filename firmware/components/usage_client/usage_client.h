#pragma once

#ifdef __cplusplus
extern "C" {
#endif

#include <stdbool.h>
#include <stdint.h>
#include <stddef.h>
#include "esp_err.h"

typedef struct {
    int64_t tokens_used;
    double  cost_usd;
    int32_t percent_used_x100;   // -1 if bridge can't compute
} usage_bucket_t;

typedef struct {
    char     model[40];
    int64_t  tokens;
    double   cost_usd;
} usage_model_t;

#define USAGE_MAX_MODELS 5

#define USAGE_MAX_OTHERS 3

typedef struct {
    double  temp_c;
    int32_t code;
    char    condition[16];
    char    icon[10];       // clear/partly/cloud/rain/snow/fog
    char    city[32];
    bool    valid;
} usage_weather_t;

typedef struct {
    double  balance;
    char    currency[8];
    double  granted;
    double  topped;
    int64_t today_tokens;
    bool    valid;
} usage_deepseek_t;

typedef struct {
    char    state[16];
    char    agent[16];
    char    event[24];
    char    asset[40];
    int32_t sessions;
    int32_t subagents;
    bool    valid;
} usage_pet_t;

typedef struct {
    char    agent[16];
    usage_bucket_t today;
    usage_bucket_t month;
    usage_bucket_t lifetime;
    usage_model_t  models[USAGE_MAX_MODELS];
    int            model_count;
    bool           valid;
} usage_other_agent_t;

#define RADAR_MAX_POINTS 11
#define RADAR_MAX_HISTORY 12

typedef struct {
    char    model[12];   // "sol", "terra", "luna", "gpt-5.5", "deepseek"
    char    effort[8];   // "ultra", "max", "xhigh"
    double  iq;
    double  price;
    double  minutes;
    int32_t passed;
    int32_t tasks;
    bool    valid;
} usage_radar_point_t;

typedef struct {
    char    model[12];
    char    effort[8];
    float   iqs[RADAR_MAX_HISTORY];
    int     iq_count;
} usage_radar_trend_t;

typedef struct {
    usage_radar_point_t points[RADAR_MAX_POINTS];
    int    point_count;
    usage_radar_trend_t trends[RADAR_MAX_POINTS];
    int    trend_count;
    bool   valid;
} usage_radar_t;

typedef struct {
    char                  updated_at[32];      // RFC3339
    char                  source[16];
    usage_bucket_t        today;
    usage_bucket_t        month;
    usage_bucket_t        lifetime;
    usage_model_t         models[USAGE_MAX_MODELS];
    int                   model_count;
    usage_weather_t       weather;
    usage_deepseek_t      deepseek;
    usage_radar_t         radar;
    usage_pet_t           pet;
    usage_other_agent_t   other[USAGE_MAX_OTHERS];
    int                   other_count;
    bool                  stale;
} usage_report_t;

// `token` may be NULL or "" for no auth; otherwise sent as X-RLCD-Token.
esp_err_t usage_client_fetch(const char *url, const char *token, usage_report_t *out);
esp_err_t usage_client_fetch_pet(const char *url, const char *token, const char *since, char *updated_at, size_t updated_at_len, usage_pet_t *out);

#ifdef __cplusplus
}
#endif
