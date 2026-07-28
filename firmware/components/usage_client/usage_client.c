#include "usage_client.h"

#include <stdlib.h>
#include <string.h>

#include "cJSON.h"
#include "esp_http_client.h"
#include "esp_log.h"

static const char *TAG = "usage_client";

#define MAX_RESP_BYTES (16 * 1024)

typedef struct {
    char *buf;
    int   len;
    int   cap;
    bool  overflow;
} resp_t;

static esp_err_t http_evt(esp_http_client_event_t *ev)
{
    if (ev->event_id == HTTP_EVENT_ON_DATA) {
        resp_t *r = (resp_t *) ev->user_data;
        if (!r || !r->buf || ev->data_len <= 0) {
            return ESP_OK;
        }
        int room = r->cap - r->len - 1;
        if (ev->data_len > room) {
            r->overflow = true;
            if (room <= 0) {
                return ESP_OK;
            }
            memcpy(r->buf + r->len, ev->data, room);
            r->len += room;
        } else {
            memcpy(r->buf + r->len, ev->data, ev->data_len);
            r->len += ev->data_len;
        }
        r->buf[r->len] = 0;
    }
    return ESP_OK;
}

static int32_t pct_x100(const cJSON *v)
{
    return cJSON_IsNumber(v) ? (int32_t)(v->valuedouble * 10000.0) : -1;
}

static void parse_bucket(const cJSON *o, usage_bucket_t *out)
{
    if (!cJSON_IsObject(o)) {
        out->percent_used_x100 = -1;
        return;
    }
    const cJSON *tok = cJSON_GetObjectItemCaseSensitive(o, "tokens_used");
    const cJSON *cst = cJSON_GetObjectItemCaseSensitive(o, "cost_usd");
    const cJSON *pct = cJSON_GetObjectItemCaseSensitive(o, "percent_used");
    out->tokens_used       = cJSON_IsNumber(tok) ? (int64_t) tok->valuedouble : 0;
    out->cost_usd          = cJSON_IsNumber(cst) ? cst->valuedouble : 0.0;
    out->percent_used_x100 = pct_x100(pct);
}


static void parse_pet(const cJSON *pet, usage_pet_t *out)
{
    if (!cJSON_IsObject(pet) || !out) return;
    const cJSON *state = cJSON_GetObjectItemCaseSensitive(pet, "state");
    const cJSON *agent = cJSON_GetObjectItemCaseSensitive(pet, "agent");
    const cJSON *event = cJSON_GetObjectItemCaseSensitive(pet, "event");
    const cJSON *asset = cJSON_GetObjectItemCaseSensitive(pet, "asset");
    const cJSON *sessions = cJSON_GetObjectItemCaseSensitive(pet, "sessions");
    const cJSON *subagents = cJSON_GetObjectItemCaseSensitive(pet, "subagents");
    if (cJSON_IsString(state)) strncpy(out->state, state->valuestring, sizeof(out->state) - 1);
    if (cJSON_IsString(agent)) strncpy(out->agent, agent->valuestring, sizeof(out->agent) - 1);
    if (cJSON_IsString(event)) strncpy(out->event, event->valuestring, sizeof(out->event) - 1);
    if (cJSON_IsString(asset)) strncpy(out->asset, asset->valuestring, sizeof(out->asset) - 1);
    out->sessions = cJSON_IsNumber(sessions) ? (int32_t) sessions->valueint : 0;
    out->subagents = cJSON_IsNumber(subagents) ? (int32_t) subagents->valueint : 0;
    out->valid = cJSON_IsString(state);
}
static void parse_models(const cJSON *bm, usage_model_t *models, int *model_count)
{
    *model_count = 0;
    if (!cJSON_IsArray(bm)) return;
    int n = cJSON_GetArraySize(bm);
    if (n > USAGE_MAX_MODELS) n = USAGE_MAX_MODELS;
    for (int i = 0; i < n; ++i) {
        const cJSON *m    = cJSON_GetArrayItem(bm, i);
        const cJSON *name = cJSON_GetObjectItemCaseSensitive(m, "model");
        const cJSON *tok  = cJSON_GetObjectItemCaseSensitive(m, "tokens");
        const cJSON *cst  = cJSON_GetObjectItemCaseSensitive(m, "cost_usd");
        if (cJSON_IsString(name)) {
            strncpy(models[i].model, name->valuestring, sizeof(models[i].model) - 1);
        }
        models[i].tokens   = cJSON_IsNumber(tok) ? (int64_t) tok->valuedouble : 0;
        models[i].cost_usd = cJSON_IsNumber(cst) ? cst->valuedouble : 0.0;
    }
    *model_count = n;
}

esp_err_t usage_client_fetch(const char *url, const char *token, usage_report_t *out)
{
    memset(out, 0, sizeof(*out));

    char *buf = (char *) malloc(MAX_RESP_BYTES);
    if (!buf) return ESP_ERR_NO_MEM;
    buf[0] = 0;
    resp_t r = { .buf = buf, .len = 0, .cap = MAX_RESP_BYTES };

    esp_http_client_config_t cfg = {
        .url           = url,
        .event_handler = http_evt,
        .user_data     = &r,
        .timeout_ms    = 30000,   // bridge keeps cache warm, but allow margin for a cold start
    };
    esp_http_client_handle_t client = esp_http_client_init(&cfg);
    if (!client) {
        free(buf);
        return ESP_ERR_NO_MEM;
    }
    if (token && token[0]) {
        esp_http_client_set_header(client, "X-RLCD-Token", token);
    }
    esp_err_t err  = esp_http_client_perform(client);
    int       code = esp_http_client_get_status_code(client);
    esp_http_client_cleanup(client);

    if (err != ESP_OK || code / 100 != 2) {
        ESP_LOGW(TAG, "GET failed err=%s status=%d", esp_err_to_name(err), code);
        free(buf);
        return ESP_FAIL;
    }
    if (r.overflow) {
        ESP_LOGW(TAG, "response too large (>%d bytes)", MAX_RESP_BYTES - 1);
        free(buf);
        return ESP_ERR_INVALID_SIZE;
    }

    cJSON *root = cJSON_Parse(buf);
    free(buf);
    if (!root) return ESP_ERR_INVALID_RESPONSE;

    const cJSON *upd = cJSON_GetObjectItemCaseSensitive(root, "updated_at");
    if (cJSON_IsString(upd)) {
        strncpy(out->updated_at, upd->valuestring, sizeof(out->updated_at) - 1);
    }
    const cJSON *src = cJSON_GetObjectItemCaseSensitive(root, "source");
    if (cJSON_IsString(src)) {
        strncpy(out->source, src->valuestring, sizeof(out->source) - 1);
    } else {
        strncpy(out->source, "ccusage", sizeof(out->source) - 1);
    }
    out->stale = cJSON_IsTrue(cJSON_GetObjectItemCaseSensitive(root, "stale"));

    const cJSON *claude = cJSON_GetObjectItemCaseSensitive(root, "claude");
    if (!cJSON_IsObject(claude)) {
        cJSON_Delete(root);
        return ESP_ERR_INVALID_RESPONSE;
    }

    parse_bucket(cJSON_GetObjectItemCaseSensitive(claude, "today"),    &out->today);
    parse_bucket(cJSON_GetObjectItemCaseSensitive(claude, "month"),    &out->month);
    parse_bucket(cJSON_GetObjectItemCaseSensitive(claude, "lifetime"), &out->lifetime);

    parse_models(cJSON_GetObjectItemCaseSensitive(claude, "by_model"), out->models, &out->model_count);

    // weather (top-level)
    const cJSON *w = cJSON_GetObjectItemCaseSensitive(root, "weather");
    if (cJSON_IsObject(w)) {
        const cJSON *t  = cJSON_GetObjectItemCaseSensitive(w, "temp_c");
        const cJSON *cd = cJSON_GetObjectItemCaseSensitive(w, "condition");
        const cJSON *ic = cJSON_GetObjectItemCaseSensitive(w, "icon");
        const cJSON *cy = cJSON_GetObjectItemCaseSensitive(w, "city");
        const cJSON *cy_ascii = cJSON_GetObjectItemCaseSensitive(w, "city_ascii");
        out->weather.temp_c = cJSON_IsNumber(t) ? t->valuedouble : 0.0;
        if (cJSON_IsString(cd)) strncpy(out->weather.condition, cd->valuestring, sizeof(out->weather.condition) - 1);
        if (cJSON_IsString(ic)) strncpy(out->weather.icon,      ic->valuestring, sizeof(out->weather.icon) - 1);
        if (cJSON_IsString(cy) && cy->valuestring[0]) {
            strncpy(out->weather.city, cy->valuestring, sizeof(out->weather.city) - 1);
        } else if (cJSON_IsString(cy_ascii) && cy_ascii->valuestring[0]) {
            strncpy(out->weather.city, cy_ascii->valuestring, sizeof(out->weather.city) - 1);
        }
        out->weather.valid = cJSON_IsNumber(t);
    }

    // deepseek (top-level)
    const cJSON *ds = cJSON_GetObjectItemCaseSensitive(root, "deepseek");
    if (cJSON_IsObject(ds)) {
        const cJSON *bal = cJSON_GetObjectItemCaseSensitive(ds, "balance");
        const cJSON *cur = cJSON_GetObjectItemCaseSensitive(ds, "currency");
        const cJSON *gr  = cJSON_GetObjectItemCaseSensitive(ds, "granted");
        const cJSON *tp  = cJSON_GetObjectItemCaseSensitive(ds, "topped");
        const cJSON *tk  = cJSON_GetObjectItemCaseSensitive(ds, "today_tokens");
        out->deepseek.balance = cJSON_IsNumber(bal) ? bal->valuedouble : 0.0;
        out->deepseek.granted = cJSON_IsNumber(gr) ? gr->valuedouble : 0.0;
        out->deepseek.topped  = cJSON_IsNumber(tp) ? tp->valuedouble : 0.0;
        out->deepseek.today_tokens = cJSON_IsNumber(tk) ? (int64_t) tk->valuedouble : 0;
        if (cJSON_IsString(cur)) strncpy(out->deepseek.currency, cur->valuestring, sizeof(out->deepseek.currency) - 1);
        else strncpy(out->deepseek.currency, "CNY", sizeof(out->deepseek.currency) - 1);
        out->deepseek.valid = cJSON_IsNumber(bal);
    }

    parse_pet(cJSON_GetObjectItemCaseSensitive(root, "pet"), &out->pet);


    const cJSON *other = cJSON_GetObjectItemCaseSensitive(root, "other");
    if (cJSON_IsArray(other)) {
        int n = cJSON_GetArraySize(other);
        if (n > USAGE_MAX_OTHERS) n = USAGE_MAX_OTHERS;
        for (int i = 0; i < n; ++i) {
            const cJSON *o = cJSON_GetArrayItem(other, i);
            if (!cJSON_IsObject(o)) continue;
            const cJSON *ag = cJSON_GetObjectItemCaseSensitive(o, "agent");
            if (cJSON_IsString(ag)) {
                strncpy(out->other[i].agent, ag->valuestring, sizeof(out->other[i].agent) - 1);
            }
            parse_bucket(cJSON_GetObjectItemCaseSensitive(o, "today"), &out->other[i].today);
            parse_bucket(cJSON_GetObjectItemCaseSensitive(o, "month"), &out->other[i].month);
            parse_bucket(cJSON_GetObjectItemCaseSensitive(o, "lifetime"), &out->other[i].lifetime);
            parse_models(cJSON_GetObjectItemCaseSensitive(o, "by_model"), out->other[i].models, &out->other[i].model_count);
            out->other[i].valid = cJSON_IsString(ag);
        }
        out->other_count = n;
    }

    // codexradar (top-level)
    const cJSON *cr = cJSON_GetObjectItemCaseSensitive(root, "codexradar");
    if (cJSON_IsObject(cr)) {
        const cJSON *avail = cJSON_GetObjectItemCaseSensitive(cr, "available");
        const cJSON *pts = cJSON_GetObjectItemCaseSensitive(cr, "points");
        if (cJSON_IsArray(pts)) {
            int n = cJSON_GetArraySize(pts);
            if (n > RADAR_MAX_POINTS) n = RADAR_MAX_POINTS;
            for (int i = 0; i < n; ++i) {
                const cJSON *p = cJSON_GetArrayItem(pts, i);
                if (!cJSON_IsObject(p)) continue;
                const cJSON *model  = cJSON_GetObjectItemCaseSensitive(p, "model");
                const cJSON *effort = cJSON_GetObjectItemCaseSensitive(p, "effort");
                const cJSON *iq     = cJSON_GetObjectItemCaseSensitive(p, "iq");
                const cJSON *price  = cJSON_GetObjectItemCaseSensitive(p, "price");
                const cJSON *mins   = cJSON_GetObjectItemCaseSensitive(p, "minutes");
                const cJSON *passed = cJSON_GetObjectItemCaseSensitive(p, "passed");
                const cJSON *tasks  = cJSON_GetObjectItemCaseSensitive(p, "tasks");
                if (cJSON_IsString(model))  strncpy(out->radar.points[i].model,  model->valuestring,  sizeof(out->radar.points[i].model) - 1);
                if (cJSON_IsString(effort)) strncpy(out->radar.points[i].effort, effort->valuestring, sizeof(out->radar.points[i].effort) - 1);
                out->radar.points[i].iq      = cJSON_IsNumber(iq)    ? iq->valuedouble    : 0.0;
                out->radar.points[i].price   = cJSON_IsNumber(price) ? price->valuedouble : 0.0;
                out->radar.points[i].minutes = cJSON_IsNumber(mins)  ? mins->valuedouble  : 0.0;
                out->radar.points[i].passed  = cJSON_IsNumber(passed)? (int32_t)passed->valueint : 0;
                out->radar.points[i].tasks   = cJSON_IsNumber(tasks) ? (int32_t)tasks->valueint  : 112;
                out->radar.points[i].valid   = cJSON_IsNumber(iq);
            }
            out->radar.point_count = n;
            out->radar.valid = cJSON_IsTrue(avail) || n > 0;
        }

        const cJSON *trends = cJSON_GetObjectItemCaseSensitive(cr, "trends");
        if (cJSON_IsArray(trends)) {
            int tn = cJSON_GetArraySize(trends);
            if (tn > RADAR_MAX_POINTS) tn = RADAR_MAX_POINTS;
            for (int i = 0; i < tn; ++i) {
                const cJSON *t = cJSON_GetArrayItem(trends, i);
                if (!cJSON_IsObject(t)) continue;
                const cJSON *tm = cJSON_GetObjectItemCaseSensitive(t, "model");
                const cJSON *te = cJSON_GetObjectItemCaseSensitive(t, "effort");
                const cJSON *iqs = cJSON_GetObjectItemCaseSensitive(t, "iqs");
                if (cJSON_IsString(tm)) strncpy(out->radar.trends[i].model, tm->valuestring, sizeof(out->radar.trends[i].model) - 1);
                if (cJSON_IsString(te)) strncpy(out->radar.trends[i].effort, te->valuestring, sizeof(out->radar.trends[i].effort) - 1);
                if (cJSON_IsArray(iqs)) {
                    int ic = cJSON_GetArraySize(iqs);
                    if (ic > RADAR_MAX_HISTORY) ic = RADAR_MAX_HISTORY;
                    for (int j = 0; j < ic; ++j) {
                        const cJSON *iq = cJSON_GetArrayItem(iqs, j);
                        out->radar.trends[i].iqs[j] = cJSON_IsNumber(iq) ? (float)iq->valuedouble : 0.0f;
                    }
                    out->radar.trends[i].iq_count = ic;
                }
            }
            out->radar.trend_count = tn;
        }
    }

    cJSON_Delete(root);
    return ESP_OK;
}

esp_err_t usage_client_fetch_pet(const char *url, const char *token, const char *since, char *updated_at, size_t updated_at_len, usage_pet_t *out)
{
    if (!out) return ESP_ERR_INVALID_ARG;
    memset(out, 0, sizeof(*out));

    char *buf = (char *) malloc(MAX_RESP_BYTES);
    if (!buf) return ESP_ERR_NO_MEM;
    buf[0] = 0;
    resp_t r = { .buf = buf, .len = 0, .cap = MAX_RESP_BYTES };
    char full_url[320];
    if (since && since[0]) snprintf(full_url, sizeof(full_url), "%s%swait=25&since=%s", url, strchr(url, '?') ? "&" : "?", since);
    else snprintf(full_url, sizeof(full_url), "%s%swait=25", url, strchr(url, '?') ? "&" : "?");

    esp_http_client_config_t cfg = {
        .url           = full_url,
        .event_handler = http_evt,
        .user_data     = &r,
        .timeout_ms    = 30000,
    };
    esp_http_client_handle_t client = esp_http_client_init(&cfg);
    if (!client) {
        free(buf);
        return ESP_ERR_NO_MEM;
    }
    if (token && token[0]) {
        esp_http_client_set_header(client, "X-RLCD-Token", token);
    }
    esp_err_t err = esp_http_client_perform(client);
    int code = esp_http_client_get_status_code(client);
    esp_http_client_cleanup(client);

    if (err != ESP_OK || code / 100 != 2) {
        ESP_LOGW(TAG, "pet GET failed err=%s status=%d", esp_err_to_name(err), code);
        free(buf);
        return ESP_FAIL;
    }
    if (r.overflow) {
        free(buf);
        return ESP_ERR_INVALID_SIZE;
    }

    cJSON *root = cJSON_Parse(buf);
    free(buf);
    if (!root) return ESP_ERR_INVALID_RESPONSE;
    parse_pet(root, out);
    if (updated_at && updated_at_len > 0) {
        const cJSON *upd = cJSON_GetObjectItemCaseSensitive(root, "updated_at");
        if (cJSON_IsString(upd)) strncpy(updated_at, upd->valuestring, updated_at_len - 1);
    }
    cJSON_Delete(root);
    return out->valid ? ESP_OK : ESP_ERR_INVALID_RESPONSE;
}
