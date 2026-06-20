// Dashboard: header (time | indoor | weather) plus a lower carousel:
//   1. CLAUDE + DEEPSEEK
//   2. DEEPSEEK + CODEX
//   3. CODEX + CLAUDE
// The fourth/fifth panels duplicate the first two so the wrap always moves left.
//
// 1-bit panel: everything is pure black/white. Amounts use a bold ASCII font
// (font_amt14); the CNY balance uses a bold ¥-capable font (font_bal28).

#include "ui_app.h"
#include "icons.h"
#include "pet_anim.h"
#include "pet_big_anim.h"
#include "lvgl.h"
#include "esp_log.h"
#include <stdio.h>
#include <string.h>

LV_FONT_DECLARE(font_amt14);   // DejaVuSans-Bold 14 (ascii + °)
LV_FONT_DECLARE(font_bal28);   // DejaVuSans-Bold 28 (digits . ¥)
LV_FONT_DECLARE(font_cn14);    // compact local Chinese UI font

#define INK   lv_color_black()
#define WHITE lv_color_white()

#define VIEW_W 400
#define SLIDE_H 222
#define PANEL_W 200
#define UNIQUE_PANELS 3
#define TRACK_PANELS 5
#define MAX_AGENT_PANELS 4
#define CAROUSEL_TIMER_MS 33
#define CAROUSEL_HOLD_TICKS 115
#define CAROUSEL_ANIM_TICKS 15
#define PET_TIMER_MS 33
#define PET_DEFAULT_FRAME_MS 70
#define PET_MAX_CATCHUP_FRAMES 3
#define PET_BIG_PAGE_H 300
#define SHOW_CLAUDE_LIMITS 0
#define FONT_CN14 (&font_cn14)

static const char *TAG = "ui_app";

typedef struct {
    lv_obj_t *bar_5h;
    lv_obj_t *bar_7d;
    lv_obj_t *lbl_5h_pct;
    lv_obj_t *lbl_7d_pct;
    lv_obj_t *lbl_reset;
    lv_obj_t *model[3];
    lv_obj_t *model_tok[3];
    lv_obj_t *tok[3];
    lv_obj_t *cost[3];
} claude_panel_t;

typedef struct {
    lv_obj_t *lbl_balance;
    lv_obj_t *val[3];
} deepseek_panel_t;

typedef struct {
    lv_obj_t *model[3];
    lv_obj_t *model_tok[3];
    lv_obj_t *tok[3];
    lv_obj_t *cost[3];
} codex_panel_t;

typedef enum {
    AGENT_CLAUDE,
    AGENT_DEEPSEEK,
    AGENT_CODEX,
} agent_kind_t;

static lv_obj_t *lbl_time, *lbl_indoor, *img_pet, *img_pet_eyes, *img_wx, *lbl_wx_temp, *lbl_wx_city, *lbl_status;
static lv_obj_t *lbl_battery_title, *lbl_battery_pct, *battery_body, *battery_fill, *battery_tip;
static lv_obj_t *pet_big_page, *img_pet_big, *img_pet_big_eyes;
static claude_panel_t   claude_panels[MAX_AGENT_PANELS];
static deepseek_panel_t deepseek_panels[MAX_AGENT_PANELS];
static codex_panel_t    codex_panels[MAX_AGENT_PANELS];
static int claude_panel_count;
static int deepseek_panel_count;
static int codex_panel_count;
static lv_obj_t *carousel_track;
static lv_obj_t *carousel_split[2];
static lv_timer_t *carousel_timer;
static int carousel_page;
static int carousel_hold_ticks;
static int carousel_anim_tick;
static int carousel_start_x;
static int carousel_target_x;
static bool carousel_animating;
static bool have_data;
static bool logged_first_report;
static char last_status_line[72] = "初始化";
static char last_wx_line[48] = "SHENZHEN";
static lv_timer_t *pet_timer;
static const pet_anim_sequence_t *pet_seq;
static const pet_anim_sequence_t *pet_big_seq;
static uint16_t pet_frame;
static uint32_t pet_last_tick;
static uint32_t pet_frame_elapsed_ms;
static const lv_image_dsc_t *pet_last_body_frame;
static const lv_image_dsc_t *pet_last_eye_frame;
static const lv_image_dsc_t *pet_big_last_body_frame;
static const lv_image_dsc_t *pet_big_last_eye_frame;

static void fmt_tok(char *o, size_t n, int64_t t)
{
    if      (t >= 1000000000LL) snprintf(o, n, "%.1fB", t / 1e9);
    else if (t >= 10000000LL)   snprintf(o, n, "%.0fM", t / 1e6);
    else if (t >= 1000000LL)    snprintf(o, n, "%.1fM", t / 1e6);
    else if (t >= 1000LL)       snprintf(o, n, "%.0fK", t / 1e3);
    else                        snprintf(o, n, "%lld", (long long) t);
}

static void fmt_cost(char *o, size_t n, double c)
{
    if      (c < 100)  snprintf(o, n, "$%.2f", c);
    else if (c < 1000) snprintf(o, n, "$%.0f", c);
    else               snprintf(o, n, "$%.1fk", c / 1000.0);
}

#if SHOW_CLAUDE_LIMITS
static int pct_clamp(int v)
{
    if (v < 0) return 0;
    if (v > 100) return 100;
    return v;
}
#endif

static lv_obj_t *mkbare(lv_obj_t *p, int x, int y, int w, int h)
{
    lv_obj_t *o = lv_obj_create(p);
    lv_obj_remove_style_all(o);
    lv_obj_clear_flag(o, LV_OBJ_FLAG_SCROLLABLE);
    lv_obj_set_pos(o, x, y);
    lv_obj_set_size(o, w, h);
    return o;
}

static lv_obj_t *mklabel(lv_obj_t *p, int x, int y, const lv_font_t *f, const char *t)
{
    lv_obj_t *l = lv_label_create(p);
    lv_obj_set_style_text_font(l, f, 0);
    lv_obj_set_style_text_color(l, INK, 0);
    lv_obj_set_pos(l, x, y);
    lv_label_set_text(l, t);
    return l;
}

static lv_obj_t *mkalign(lv_obj_t *p, int left_x, int y, int w, lv_text_align_t a,
                         const lv_font_t *f, const char *t)
{
    lv_obj_t *l = lv_label_create(p);
    lv_obj_set_style_text_font(l, f, 0);
    lv_obj_set_style_text_color(l, INK, 0);
    lv_obj_set_width(l, w);
    lv_obj_set_style_text_align(l, a, 0);
    lv_label_set_long_mode(l, LV_LABEL_LONG_CLIP);
    lv_obj_set_pos(l, left_x, y);
    lv_label_set_text(l, t);
    return l;
}

static void mkdiv(lv_obj_t *p, int x, int y, int w, int h)
{
    lv_obj_t *d = mkbare(p, x, y, w, h);
    lv_obj_set_style_bg_color(d, INK, 0);
    lv_obj_set_style_bg_opa(d, LV_OPA_COVER, 0);
}

static lv_obj_t *mkdiv_obj(lv_obj_t *p, int x, int y, int w, int h)
{
    lv_obj_t *d = mkbare(p, x, y, w, h);
    lv_obj_set_style_bg_color(d, INK, 0);
    lv_obj_set_style_bg_opa(d, LV_OPA_COVER, 0);
    return d;
}

static lv_obj_t *mkbattery_body(lv_obj_t *p, int x, int y, int w, int h)
{
    lv_obj_t *o = mkbare(p, x, y, w, h);
    lv_obj_set_style_bg_color(o, WHITE, 0);
    lv_obj_set_style_bg_opa(o, LV_OPA_COVER, 0);
    lv_obj_set_style_border_color(o, INK, 0);
    lv_obj_set_style_border_width(o, 1, 0);
    lv_obj_set_style_radius(o, 0, 0);
    return o;
}

#if SHOW_CLAUDE_LIMITS
static lv_obj_t *mkbar(lv_obj_t *p, int x, int y, int w)
{
    lv_obj_t *b = lv_bar_create(p);
    lv_obj_set_pos(b, x, y);
    lv_obj_set_size(b, w, 13);
    lv_bar_set_range(b, 0, 100);
    lv_obj_set_style_radius(b, 6, LV_PART_MAIN);
    lv_obj_set_style_bg_color(b, WHITE, LV_PART_MAIN);
    lv_obj_set_style_bg_opa(b, LV_OPA_COVER, LV_PART_MAIN);
    lv_obj_set_style_border_color(b, INK, LV_PART_MAIN);
    lv_obj_set_style_border_width(b, 2, LV_PART_MAIN);
    lv_obj_set_style_radius(b, 6, LV_PART_INDICATOR);
    lv_obj_set_style_bg_color(b, INK, LV_PART_INDICATOR);
    lv_bar_set_value(b, 0, LV_ANIM_OFF);
    return b;
}
#endif

static lv_obj_t *mkicon(lv_obj_t *p, int x, int y, const lv_image_dsc_t *src)
{
    lv_obj_t *im = lv_image_create(p);
    lv_image_set_src(im, src);
    lv_obj_set_pos(im, x, y);
    lv_obj_set_style_image_recolor(im, INK, 0);
    lv_obj_set_style_image_recolor_opa(im, LV_OPA_COVER, 0);
    return im;
}

static uint32_t pet_frame_duration(const pet_anim_sequence_t *seq, uint16_t frame)
{
    if (!seq || !seq->durations_ms || frame >= seq->frame_count) return PET_DEFAULT_FRAME_MS;
    uint16_t duration = seq->durations_ms[frame];
    if (duration < PET_TIMER_MS) return PET_TIMER_MS;
    return duration ? duration : PET_DEFAULT_FRAME_MS;
}

static void pet_show_frame(void)
{
    if (!img_pet || !pet_seq || !pet_seq->frames || pet_seq->frame_count == 0) return;
    if (pet_frame >= pet_seq->frame_count) pet_frame = 0;
    uint16_t frame = (uint16_t) (pet_frame % pet_seq->frame_count);
    const lv_image_dsc_t *body = pet_seq->frames[frame];
    if (body != pet_last_body_frame) {
        lv_image_set_src(img_pet, body);
        pet_last_body_frame = body;
    }
    if (img_pet_big && pet_big_seq && pet_big_seq->frames && pet_big_seq->frame_count > 0) {
        uint16_t big_frame = (uint16_t) (pet_frame % pet_big_seq->frame_count);
        const lv_image_dsc_t *big_body = pet_big_seq->frames[big_frame];
        if (big_body != pet_big_last_body_frame) {
            lv_image_set_src(img_pet_big, big_body);
            pet_big_last_body_frame = big_body;
        }
    }
}

static void pet_set_sequence(const pet_anim_sequence_t *seq, const pet_anim_sequence_t *big_seq)
{
    if (!seq) seq = ui_pet_anim_idle();
    if (!big_seq) big_seq = ui_pet_big_anim_idle();
    if (seq == pet_seq && big_seq == pet_big_seq) return;
    pet_seq = seq;
    pet_big_seq = big_seq;
    pet_frame = 0;
    pet_frame_elapsed_ms = 0;
    pet_last_tick = lv_tick_get();
    pet_last_body_frame = NULL;
    pet_last_eye_frame = NULL;
    pet_big_last_body_frame = NULL;
    pet_big_last_eye_frame = NULL;
    pet_show_frame();
    if (pet_timer) lv_timer_set_period(pet_timer, PET_TIMER_MS);
}

static void pet_timer_cb(lv_timer_t *timer)
{
    (void) timer;
    if (!pet_seq || pet_seq->frame_count == 0) return;
    uint32_t now = lv_tick_get();
    uint32_t delta = pet_last_tick ? (now - pet_last_tick) : PET_TIMER_MS;
    pet_last_tick = now;
    if (delta > 500) delta = 500;
    pet_frame_elapsed_ms += delta;

    bool changed = false;
    uint8_t catchup = 0;
    while (catchup < PET_MAX_CATCHUP_FRAMES) {
        uint32_t duration = pet_frame_duration(pet_seq, pet_frame);
        if (pet_frame_elapsed_ms < duration) break;
        pet_frame_elapsed_ms -= duration;
        pet_frame = (uint16_t) ((pet_frame + 1) % pet_seq->frame_count);
        changed = true;
        ++catchup;
    }
    if (changed) pet_show_frame();
    if (pet_timer) lv_timer_set_period(pet_timer, PET_TIMER_MS);
}

static lv_obj_t *mkpet_sized(lv_obj_t *p, int x, int y, int w, int h)
{
    lv_obj_t *im = lv_image_create(p);
    lv_obj_set_pos(im, x, y);
    lv_obj_set_size(im, w, h);
    return im;
}

static lv_obj_t *mkpet(lv_obj_t *p, int x, int y)
{
    return mkpet_sized(p, x, y, 56, 56);
}

static void short_model_name(const char *name, char *out, size_t n)
{
    if (!out || n == 0) return;
    if (!name || !name[0]) {
        snprintf(out, n, "-");
        return;
    }
    if (strncmp(name, "deepseek-", 9) == 0) {
        snprintf(out, n, "ds-%s", name + 9);
    } else if (strncmp(name, "claude-", 7) == 0) {
        snprintf(out, n, "%s", name + 7);
    } else {
        snprintf(out, n, "%s", name);
    }
    if (strlen(out) > 16) out[16] = '\0';
}

static void mk_model_rows(lv_obj_t *slide, int x, lv_obj_t *model[3], lv_obj_t *model_tok[3])
{
    for (int i = 0; i < 3; ++i) {
        int y = 46 + i * 24;
        model[i] = mkalign(slide, x + 12, y, 112, LV_TEXT_ALIGN_LEFT, &font_amt14, "-");
        model_tok[i] = mkalign(slide, x + 132, y, 60, LV_TEXT_ALIGN_RIGHT, &font_amt14, "-");
    }
}

static void mk_claude_panel(lv_obj_t *slide, int x)
{
    if (claude_panel_count >= MAX_AGENT_PANELS) return;
    claude_panel_t *p = &claude_panels[claude_panel_count++];
    memset(p, 0, sizeof(*p));

    mkicon(slide, x + 10, 6, &icon_claudecode);
    mklabel(slide, x + 50, 10, &lv_font_montserrat_20, "CLAUDE");
#if SHOW_CLAUDE_LIMITS
    mklabel(slide, x + 12, 46, &lv_font_montserrat_14, "5h");
    p->bar_5h = mkbar(slide, x + 40, 46, 96);
    p->lbl_5h_pct = mkalign(slide, x + 140, 44, 52, LV_TEXT_ALIGN_RIGHT, &font_amt14, "--%");
    mklabel(slide, x + 12, 70, &lv_font_montserrat_14, "7d");
    p->bar_7d = mkbar(slide, x + 40, 70, 96);
    p->lbl_7d_pct = mkalign(slide, x + 140, 68, 52, LV_TEXT_ALIGN_RIGHT, &font_amt14, "--%");
    p->lbl_reset = mklabel(slide, x + 12, 94, &lv_font_montserrat_14, "reset --");
    mkdiv(slide, x + 12, 118, 178, 1);
    const int row_y0 = 126;
    const int row_gap = 28;
#else
    mk_model_rows(slide, x, p->model, p->model_tok);
    mkdiv(slide, x + 12, 118, 178, 1);
    const int row_y0 = 126;
    const int row_gap = 28;
#endif

    const char *rows[3] = {"今日", "本月", "合计"};
    for (int i = 0; i < 3; ++i) {
        int y = row_y0 + i * row_gap;
        mklabel(slide, x + 12, y, FONT_CN14, rows[i]);
        p->tok[i]  = mkalign(slide, x + 64, y, 62, LV_TEXT_ALIGN_RIGHT, &font_amt14, "-");
        p->cost[i] = mkalign(slide, x + 128, y, 64, LV_TEXT_ALIGN_RIGHT, &font_amt14, "-");
    }
}

static void mk_deepseek_panel(lv_obj_t *slide, int x)
{
    if (deepseek_panel_count >= MAX_AGENT_PANELS) return;
    deepseek_panel_t *p = &deepseek_panels[deepseek_panel_count++];

    mkicon(slide, x + 10, 6, &icon_deepseek);
    mklabel(slide, x + 50, 10, &lv_font_montserrat_20, "DEEPSEEK");
    mkalign(slide, x + 12, 38, 176, LV_TEXT_ALIGN_CENTER, FONT_CN14, "可用");
    p->lbl_balance = mkalign(slide, x + 12, 62, 176, LV_TEXT_ALIGN_CENTER, &font_bal28, "--");
    mkdiv(slide, x + 12, 118, 178, 1);

    const char *rows[3] = {"送值", "充值", "今日token"};
    for (int i = 0; i < 3; ++i) {
        int y = 126 + i * 28;
        mklabel(slide, x + 12, y, FONT_CN14, rows[i]);
        p->val[i] = mkalign(slide, x + 94, y, 94, LV_TEXT_ALIGN_RIGHT, &font_amt14, "-");
    }
}

static void mk_codex_panel(lv_obj_t *slide, int x)
{
    if (codex_panel_count >= MAX_AGENT_PANELS) return;
    codex_panel_t *p = &codex_panels[codex_panel_count++];

    mkicon(slide, x + 10, 6, &icon_codex);
    mklabel(slide, x + 50, 10, &lv_font_montserrat_20, "CODEX");
    mk_model_rows(slide, x, p->model, p->model_tok);
    mkdiv(slide, x + 12, 118, 178, 1);

    const char *rows[3] = {"今日", "本月", "合计"};
    for (int i = 0; i < 3; ++i) {
        int y = 126 + i * 28;
        mklabel(slide, x + 12, y, FONT_CN14, rows[i]);
        p->tok[i]  = mkalign(slide, x + 64, y, 62, LV_TEXT_ALIGN_RIGHT, &font_amt14, "-");
        p->cost[i] = mkalign(slide, x + 128, y, 64, LV_TEXT_ALIGN_RIGHT, &font_amt14, "-");
    }
}

static void mk_agent_panel(lv_obj_t *slide, int x, agent_kind_t kind)
{
    if (kind == AGENT_CLAUDE) mk_claude_panel(slide, x);
    else if (kind == AGENT_DEEPSEEK) mk_deepseek_panel(slide, x);
    else mk_codex_panel(slide, x);
}

static void mk_track_panel(lv_obj_t *track, int index, agent_kind_t kind)
{
    mk_agent_panel(track, index * PANEL_W, kind);
}

static void set_carousel_splits(bool moving)
{
    if (!carousel_split[0] || !carousel_split[1]) return;
    lv_obj_set_x(carousel_split[0], (carousel_page + 1) * PANEL_W - 2);
    lv_obj_clear_flag(carousel_split[0], LV_OBJ_FLAG_HIDDEN);
    if (moving) {
        lv_obj_set_x(carousel_split[1], (carousel_page + 2) * PANEL_W - 2);
        lv_obj_clear_flag(carousel_split[1], LV_OBJ_FLAG_HIDDEN);
    } else {
        lv_obj_add_flag(carousel_split[1], LV_OBJ_FLAG_HIDDEN);
    }
}

static void carousel_timer_cb(lv_timer_t *timer)
{
    (void) timer;
    if (!carousel_track) return;

    if (!carousel_animating) {
        if (++carousel_hold_ticks < CAROUSEL_HOLD_TICKS) return;
        carousel_hold_ticks = 0;
        carousel_animating = true;
        carousel_anim_tick = 0;
        carousel_start_x = lv_obj_get_x(carousel_track);
        set_carousel_splits(true);
        carousel_page += 1;
        carousel_target_x = -carousel_page * PANEL_W;
    }

    ++carousel_anim_tick;
    int x = carousel_start_x +
            ((carousel_target_x - carousel_start_x) * carousel_anim_tick) / CAROUSEL_ANIM_TICKS;
    lv_obj_set_x(carousel_track, x);

    if (carousel_anim_tick >= CAROUSEL_ANIM_TICKS) {
        lv_obj_set_x(carousel_track, carousel_target_x);
        carousel_animating = false;
        if (carousel_page >= UNIQUE_PANELS) {
            carousel_page = 0;
            lv_obj_set_x(carousel_track, 0);
        }
        set_carousel_splits(false);
    }
}

static void mk_pet_big_page(lv_obj_t *screen)
{
    const pet_anim_sequence_t *idle = ui_pet_big_anim_idle();
    int pet_w = idle ? idle->width : 184;
    int pet_h = idle ? idle->height : 184;
    int pet_x = (VIEW_W - pet_w) / 2;
    int pet_y = (PET_BIG_PAGE_H - pet_h) / 2;

    pet_big_page = mkbare(screen, 0, 0, VIEW_W, PET_BIG_PAGE_H);
    lv_obj_set_style_bg_color(pet_big_page, WHITE, 0);
    lv_obj_set_style_bg_opa(pet_big_page, LV_OPA_COVER, 0);
    img_pet_big = mkpet_sized(pet_big_page, pet_x, pet_y, pet_w, pet_h);
    img_pet_big_eyes = NULL;
    lv_obj_add_flag(pet_big_page, LV_OBJ_FLAG_HIDDEN);
}

void ui_app_init(void)
{
    lv_obj_t *s = lv_screen_active();
    lv_obj_set_style_bg_color(s, WHITE, 0);
    lv_obj_set_style_bg_opa(s, LV_OPA_COVER, 0);

    claude_panel_count = 0;
    deepseek_panel_count = 0;
    codex_panel_count = 0;
    carousel_page = 0;
    carousel_hold_ticks = 0;
    carousel_anim_tick = 0;
    carousel_start_x = 0;
    carousel_target_x = 0;
    carousel_animating = false;

    lbl_time   = mklabel(s, 10, 4, &lv_font_montserrat_28, "--:--");
    lbl_indoor = mklabel(s, 12, 44, FONT_CN14, "温度 --.-\xE2\x84\x83""  湿度 --%");
    img_pet    = mkpet(s, 164, 5);
    img_pet_eyes = NULL;
    img_wx     = mkicon(s, 280, 8, &icon_wx_cloud);
    lbl_wx_temp = mkalign(s, 308, 10, 80, LV_TEXT_ALIGN_RIGHT, &lv_font_montserrat_20, "--\xC2\xB0""C");
    lbl_wx_city = mkalign(s, 208, 44, 180, LV_TEXT_ALIGN_RIGHT, FONT_CN14, "等待");
    mkdiv(s, 10, 66, 380, 2);
    mkdiv(s, 10, 268, 380, 2);
    lbl_status = mkalign(s, 12, 274, 276, LV_TEXT_ALIGN_LEFT, FONT_CN14, last_status_line);
    lbl_battery_title = mkalign(s, 292, 274, 96, LV_TEXT_ALIGN_LEFT, FONT_CN14, "电量");
    battery_body = mkbattery_body(s, 326, 278, 20, 9);
    battery_fill = mkdiv_obj(s, 328, 280, 1, 5);
    battery_tip = mkdiv_obj(s, 348, 281, 2, 3);
    lbl_battery_pct = mkalign(s, 352, 274, 36, LV_TEXT_ALIGN_RIGHT, &font_amt14, "--%");
    lv_obj_add_flag(battery_fill, LV_OBJ_FLAG_HIDDEN);

    lv_obj_t *view = mkbare(s, 0, 66, VIEW_W, SLIDE_H);
    carousel_track = mkbare(view, 0, 0, PANEL_W * TRACK_PANELS, SLIDE_H);
    mk_track_panel(carousel_track, 0, AGENT_CLAUDE);
    mk_track_panel(carousel_track, 1, AGENT_DEEPSEEK);
    mk_track_panel(carousel_track, 2, AGENT_CODEX);
    mk_track_panel(carousel_track, 3, AGENT_CLAUDE);
    mk_track_panel(carousel_track, 4, AGENT_DEEPSEEK);
    carousel_split[0] = mkdiv_obj(carousel_track, PANEL_W - 2, 8, 2, 188);
    carousel_split[1] = mkdiv_obj(carousel_track, PANEL_W * 2 - 2, 8, 2, 188);
    set_carousel_splits(false);

    mk_pet_big_page(s);

    if (carousel_timer) lv_timer_del(carousel_timer);
    carousel_timer = lv_timer_create(carousel_timer_cb, CAROUSEL_TIMER_MS, NULL);
    pet_seq = NULL;
    pet_big_seq = NULL;
    pet_frame = 0;
    pet_set_sequence(ui_pet_anim_idle(), ui_pet_big_anim_idle());
    if (pet_timer) lv_timer_del(pet_timer);
    pet_timer = lv_timer_create(pet_timer_cb, PET_TIMER_MS, NULL);
    have_data = false;
    logged_first_report = false;
    ESP_LOGI(TAG, "UI build marker UI v13, model_col=112, token_col=60, pet_eye_knockout=1, carousel=30fps-fast, pet_art=crab-focus, battery=1, pet_big_page=1, pet_big_size=184");
}

static const char *weather_cn(const char *condition)
{
    if (!condition || !condition[0]) return "";
    if (strstr(condition, "Clear") || strstr(condition, "Sunny")) return "晴";
    if (strstr(condition, "Partly")) return "多云";
    if (strstr(condition, "Cloud") || strstr(condition, "Overcast")) return "多云";
    if (strstr(condition, "Rain") || strstr(condition, "Drizzle") || strstr(condition, "Storm")) return "雨";
    if (strstr(condition, "Snow")) return "雪";
    if (strstr(condition, "Fog") || strstr(condition, "Haze")) return "雾";
    return condition;
}

static const lv_image_dsc_t *wx_icon(const char *key)
{
    if (!strcmp(key, "clear"))  return &icon_wx_clear;
    if (!strcmp(key, "partly")) return &icon_wx_partly;
    if (!strcmp(key, "rain"))   return &icon_wx_rain;
    if (!strcmp(key, "snow"))   return &icon_wx_snow;
    if (!strcmp(key, "fog"))    return &icon_wx_fog;
    return &icon_wx_cloud;
}

static void update_claude_panel(const claude_panel_t *p, const usage_report_t *r)
{
    char tk[16], ct[16];
#if SHOW_CLAUDE_LIMITS
    char b[40];
    int p5 = r->limits.util_5h_x100;
    int p7 = r->limits.util_7d_x100;

    if (p5 >= 0) {
        int clamped = pct_clamp(p5);
        lv_bar_set_value(p->bar_5h, clamped, LV_ANIM_OFF);
        snprintf(b, sizeof(b), "%d%%", clamped);
        lv_label_set_text(p->lbl_5h_pct, b);
    } else {
        lv_bar_set_value(p->bar_5h, 0, LV_ANIM_OFF);
        lv_label_set_text(p->lbl_5h_pct, "--%");
    }

    if (p7 >= 0) {
        int clamped = pct_clamp(p7);
        lv_bar_set_value(p->bar_7d, clamped, LV_ANIM_OFF);
        snprintf(b, sizeof(b), "%d%%", clamped);
        lv_label_set_text(p->lbl_7d_pct, b);
    } else {
        lv_bar_set_value(p->bar_7d, 0, LV_ANIM_OFF);
        lv_label_set_text(p->lbl_7d_pct, "--%");
    }

    int m = r->limits.reset_5h_min;
    if (m >= 0) snprintf(b, sizeof(b), "reset in %dh%02dm", m / 60, m % 60);
    else        snprintf(b, sizeof(b), "reset --");
    lv_label_set_text(p->lbl_reset, b);
#else
    for (int i = 0; i < 3; ++i) {
        if (i < r->model_count) {
            fmt_tok(tk, sizeof(tk), r->models[i].tokens);
            char name[32]; short_model_name(r->models[i].model, name, sizeof(name));
            lv_label_set_text(p->model[i], name);
            lv_label_set_text(p->model_tok[i], tk);
        } else {
            lv_label_set_text(p->model[i], "-");
            lv_label_set_text(p->model_tok[i], "-");
        }
    }
#endif

    const usage_bucket_t *cb[3] = { &r->today, &r->month, &r->lifetime };
    for (int i = 0; i < 3; ++i) {
        fmt_tok(tk, sizeof(tk), cb[i]->tokens_used);
        fmt_cost(ct, sizeof(ct), cb[i]->cost_usd);
        lv_label_set_text(p->tok[i], tk);
        lv_label_set_text(p->cost[i], ct);
    }
}

static void update_deepseek_panel(const deepseek_panel_t *p, const usage_report_t *r)
{
    char b[24], tk[16];
    if (r->deepseek.valid) {
        snprintf(b, sizeof(b), "\xC2\xA5""%.2f", r->deepseek.balance);  // ¥
        lv_label_set_text(p->lbl_balance, b);
        snprintf(b, sizeof(b), "%.2f", r->deepseek.granted);
        lv_label_set_text(p->val[0], b);
        snprintf(b, sizeof(b), "%.2f", r->deepseek.topped);
        lv_label_set_text(p->val[1], b);
        fmt_tok(tk, sizeof(tk), r->deepseek.today_tokens);
        lv_label_set_text(p->val[2], tk);
    } else {
        lv_label_set_text(p->lbl_balance, "--");
        lv_label_set_text(p->val[0], "-");
        lv_label_set_text(p->val[1], "-");
        lv_label_set_text(p->val[2], "-");
    }
}

static const usage_other_agent_t *find_other_agent(const usage_report_t *r, const char *agent)
{
    for (int i = 0; i < r->other_count; ++i) {
        if (r->other[i].valid && strcmp(r->other[i].agent, agent) == 0) {
            return &r->other[i];
        }
    }
    return NULL;
}

static void update_codex_panel(const codex_panel_t *p, const usage_report_t *r)
{
    char tk[16], ct[16];
    const usage_other_agent_t *codex = find_other_agent(r, "codex");
    if (!codex) {
        for (int i = 0; i < 3; ++i) {
            lv_label_set_text(p->model[i], "-");
            lv_label_set_text(p->model_tok[i], "-");
            lv_label_set_text(p->tok[i], "-");
            lv_label_set_text(p->cost[i], "-");
        }
        return;
    }

    for (int i = 0; i < 3; ++i) {
        if (i < codex->model_count) {
            fmt_tok(tk, sizeof(tk), codex->models[i].tokens);
            char name[32]; short_model_name(codex->models[i].model, name, sizeof(name));
            lv_label_set_text(p->model[i], name);
            lv_label_set_text(p->model_tok[i], tk);
        } else {
            lv_label_set_text(p->model[i], "-");
            lv_label_set_text(p->model_tok[i], "-");
        }
    }

    const usage_bucket_t *cb[3] = { &codex->today, &codex->month, &codex->lifetime };
    for (int i = 0; i < 3; ++i) {
        fmt_tok(tk, sizeof(tk), cb[i]->tokens_used);
        fmt_cost(ct, sizeof(ct), cb[i]->cost_usd);
        lv_label_set_text(p->tok[i], tk);
        lv_label_set_text(p->cost[i], ct);
    }
}

void ui_app_update(const usage_report_t *r)
{
    if (!r) return;
    have_data = true;
    if (!logged_first_report) {
        char short_name[32];
        const char *raw_name = (r->model_count > 0) ? r->models[0].model : "-";
        short_model_name(raw_name, short_name, sizeof(short_name));
        long long top_tokens = (r->model_count > 0) ? (long long) r->models[0].tokens : 0;
        ESP_LOGI(TAG, "UI v10 first report: deepseek_valid=%d balance=%.2f top_model=%s short=%s top_tokens=%lld",
                 r->deepseek.valid ? 1 : 0, r->deepseek.balance, raw_name, short_name, top_tokens);
        logged_first_report = true;
    }

    for (int i = 0; i < claude_panel_count; ++i) {
        update_claude_panel(&claude_panels[i], r);
    }
    for (int i = 0; i < deepseek_panel_count; ++i) {
        update_deepseek_panel(&deepseek_panels[i], r);
    }
    for (int i = 0; i < codex_panel_count; ++i) {
        update_codex_panel(&codex_panels[i], r);
    }
    if (r->pet.valid) {
        const pet_anim_sequence_t *seq = r->pet.asset[0]
            ? ui_pet_anim_for_asset(r->pet.asset)
            : ui_pet_anim_for_state(r->pet.state);
        const pet_anim_sequence_t *big_seq = r->pet.asset[0]
            ? ui_pet_big_anim_for_asset(r->pet.asset)
            : ui_pet_big_anim_for_state(r->pet.state);
        pet_set_sequence(seq, big_seq);
    }

    if (r->weather.valid) {
        lv_image_set_src(img_wx, wx_icon(r->weather.icon));
        char b[16]; snprintf(b, sizeof(b), "%.0f\xC2\xB0""C", r->weather.temp_c);
        lv_label_set_text(lbl_wx_temp, b);
        snprintf(last_wx_line, sizeof(last_wx_line), "%s  %s", r->weather.city, weather_cn(r->weather.condition));
    }
    lv_label_set_text(lbl_wx_city, r->stale ? "等待" : last_wx_line);
    if (lbl_status) {
        const char *updated = "--:--";
        if (r->updated_at[2] == ':' ) updated = r->updated_at;
        else if (r->updated_at[11]) updated = r->updated_at + 11;
        snprintf(last_status_line, sizeof(last_status_line), "%s 来源 %s  更新 %.5s",
                 r->stale ? "离线" : "在线", r->source[0] ? r->source : "ccusage", updated);
        lv_label_set_text(lbl_status, last_status_line);
    }
}


void ui_app_update_pet(const usage_pet_t *pet)
{
    if (!pet || !pet->valid) return;
    const pet_anim_sequence_t *seq = pet->asset[0]
        ? ui_pet_anim_for_asset(pet->asset)
        : ui_pet_anim_for_state(pet->state);
    const pet_anim_sequence_t *big_seq = pet->asset[0]
        ? ui_pet_big_anim_for_asset(pet->asset)
        : ui_pet_big_anim_for_state(pet->state);
    pet_set_sequence(seq, big_seq);
}

void ui_app_toggle_pet_page(void)
{
    if (!pet_big_page) return;
    if (lv_obj_has_flag(pet_big_page, LV_OBJ_FLAG_HIDDEN)) {
        lv_obj_clear_flag(pet_big_page, LV_OBJ_FLAG_HIDDEN);
        lv_obj_move_foreground(pet_big_page);
        pet_show_frame();
    } else {
        lv_obj_add_flag(pet_big_page, LV_OBJ_FLAG_HIDDEN);
    }
}

void ui_app_set_env(float temp_c, float humidity, bool ok)
{
    char b[40];
    if (ok) snprintf(b, sizeof(b), "温度 %.1f\xE2\x84\x83""  湿度 %.0f%%", temp_c, humidity);
    else    snprintf(b, sizeof(b), "温度 --.-\xE2\x84\x83""  湿度 --%%");
    lv_label_set_text(lbl_indoor, b);
}

static void set_battery_parts_hidden(bool hidden)
{
    lv_obj_t *parts[] = { battery_body, battery_fill, battery_tip };
    for (size_t i = 0; i < sizeof(parts) / sizeof(parts[0]); ++i) {
        if (!parts[i]) continue;
        if (hidden) lv_obj_add_flag(parts[i], LV_OBJ_FLAG_HIDDEN);
        else lv_obj_clear_flag(parts[i], LV_OBJ_FLAG_HIDDEN);
    }
}

void ui_app_set_battery(float voltage_v, int percent, bool ok, bool typec_power)
{
    char b[20];
    if (ok && typec_power) {
        if (lbl_battery_title) lv_label_set_text(lbl_battery_title, "TYPE-C 供电");
        if (lbl_battery_pct) lv_obj_add_flag(lbl_battery_pct, LV_OBJ_FLAG_HIDDEN);
        set_battery_parts_hidden(true);
        (void)voltage_v;
        return;
    } else if (ok) {
        if (lbl_battery_title) lv_label_set_text(lbl_battery_title, "电量");
        if (lbl_battery_pct) lv_obj_clear_flag(lbl_battery_pct, LV_OBJ_FLAG_HIDDEN);
        set_battery_parts_hidden(false);
        if (percent < 0) percent = 0;
        if (percent > 100) percent = 100;
        snprintf(b, sizeof(b), "%d%%", percent);
        if (battery_fill) {
            int fill_w = (percent * 16 + 50) / 100;
            if (fill_w <= 0) {
                lv_obj_add_flag(battery_fill, LV_OBJ_FLAG_HIDDEN);
            } else {
                lv_obj_clear_flag(battery_fill, LV_OBJ_FLAG_HIDDEN);
                lv_obj_set_width(battery_fill, fill_w);
            }
        }
    } else {
        if (lbl_battery_title) lv_label_set_text(lbl_battery_title, "电量");
        if (lbl_battery_pct) lv_obj_clear_flag(lbl_battery_pct, LV_OBJ_FLAG_HIDDEN);
        snprintf(b, sizeof(b), "--%%");
        set_battery_parts_hidden(false);
        if (battery_fill) lv_obj_add_flag(battery_fill, LV_OBJ_FLAG_HIDDEN);
    }
    if (lbl_battery_pct) lv_label_set_text(lbl_battery_pct, b);
    (void)voltage_v;
}

void ui_app_set_time(const char *hm)
{
    if (lbl_time) lv_label_set_text(lbl_time, hm);
}

static void set_all_claude_reset(const char *text)
{
#if SHOW_CLAUDE_LIMITS
    for (int i = 0; i < claude_panel_count; ++i) {
        lv_label_set_text(claude_panels[i].lbl_reset, text);
    }
#else
    (void)text;
#endif
}

void ui_app_mark_stale(void)
{
    if (have_data) {
        lv_label_set_text(lbl_wx_city, "STALE");
        if (lbl_status) lv_label_set_text(lbl_status, "离线 来源 bridge  更新 --:--");
    } else {
        set_all_claude_reset("等待服务");
        lv_label_set_text(lbl_wx_city, "等待服务");
        if (lbl_status) lv_label_set_text(lbl_status, "离线 来源 bridge  更新 --:--");
    }
}
