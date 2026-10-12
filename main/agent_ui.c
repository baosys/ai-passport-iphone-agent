#include "agent_ui.h"

#include "agent_bridge.h"
#include "agent_hid.h"

#include <stdio.h>
#include <string.h>

#include "bsp_display.h"
#include "esp_timer.h"
#include "lvgl.h"

// 深色面板:屏幕常在手机旁边,暗底更省电,状态色更醒目。
#define COL_BG      0x0E1116
#define COL_CARD    0x1A1F29
#define COL_TEXT    0xE8ECF2
#define COL_MUTED   0x8A94A6
#define COL_GREEN   0x3DDC84
#define COL_AMBER   0xFFB020
#define COL_BLUE    0x5B8CFF
#define COL_RED     0xFF5A5F

#define REFRESH_MS      200
#define DIM_AFTER_US    (30LL * 1000 * 1000)   // 30 s 无交互后调暗背光
#define BRIGHT_PERCENT  100
#define DIM_PERCENT     8

// 屏幕四角有 30 px 圆角遮罩;内容横向留 16 px 边距,标题/页脚离开圆角区。
#define MARGIN 16
#define CARD_W (240 - 2 * MARGIN)
#define CARD_PAD 10
#define ROW_H 15
#define RECENT_ROWS 3   // 活动卡里显示的最近命令条数(受 82 px 卡高限制)
#define PHONE_Y 48
#define LINK_Y 130
#define ACT_Y 212
#define STATUS_H 76
#define ACT_H 82

static lv_obj_t *s_live, *s_setup;
static lv_obj_t *s_battery;
static lv_obj_t *s_phone_state, *s_phone_sub;
static lv_obj_t *s_link_state, *s_link_sub;
static lv_obj_t *s_count;
static lv_obj_t *s_recent_cmd[RECENT_ROWS], *s_recent_ms[RECENT_ROWS];
static lv_obj_t *s_paused;
static lv_timer_t *s_timer;

static const char *s_ble_name;
static bool s_wifi_configured;
static volatile int s_soc = -1;
static volatile int64_t s_wake_at;
static bool s_dim;
static uint32_t s_seen_seq;
static agent_hid_state_t s_seen_hid = AGENT_HID_OFF;

static lv_obj_t *label(lv_obj_t *parent, const lv_font_t *font, uint32_t color, const char *text)
{
    lv_obj_t *l = lv_label_create(parent);
    lv_obj_set_style_text_font(l, font, 0);
    lv_obj_set_style_text_color(l, lv_color_hex(color), 0);
    lv_label_set_text(l, text);
    return l;
}

static lv_obj_t *screen(void)
{
    lv_obj_t *scr = lv_obj_create(NULL);
    lv_obj_set_style_bg_color(scr, lv_color_hex(COL_BG), 0);
    lv_obj_set_style_bg_opa(scr, LV_OPA_COVER, 0);
    lv_obj_clear_flag(scr, LV_OBJ_FLAG_SCROLLABLE);
    return scr;
}

static lv_obj_t *card(lv_obj_t *parent, int y, int h)
{
    lv_obj_t *c = lv_obj_create(parent);
    lv_obj_set_pos(c, MARGIN, y);
    lv_obj_set_size(c, CARD_W, h);
    lv_obj_set_style_bg_color(c, lv_color_hex(COL_CARD), 0);
    lv_obj_set_style_border_width(c, 0, 0);
    lv_obj_set_style_radius(c, 12, 0);
    lv_obj_set_style_pad_all(c, CARD_PAD, 0);
    lv_obj_clear_flag(c, LV_OBJ_FLAG_SCROLLABLE);
    return c;
}

// 卡片:左上图标 + 小标题,下方一行大号状态与一行说明。
static void status_card(lv_obj_t *parent, int y, const char *icon_title,
                        lv_obj_t **state, lv_obj_t **sub)
{
    lv_obj_t *c = card(parent, y, STATUS_H);
    label(c, &lv_font_montserrat_14, COL_MUTED, icon_title);
    *state = label(c, &lv_font_montserrat_20, COL_TEXT, "");
    lv_obj_set_pos(*state, 0, 18);
    *sub = label(c, &lv_font_montserrat_14, COL_MUTED, "");
    lv_obj_set_pos(*sub, 0, 40);
    lv_obj_set_width(*sub, CARD_W - 2 * CARD_PAD);
    lv_label_set_long_mode(*sub, LV_LABEL_LONG_DOT);
}

static void header(lv_obj_t *scr, const char *title, bool with_battery)
{
    lv_obj_t *t = label(scr, &lv_font_montserrat_20, COL_TEXT, title);
    lv_obj_set_pos(t, 24, 16);
    if (with_battery) {
        s_battery = label(scr, &lv_font_montserrat_14, COL_MUTED, "");
        lv_obj_align(s_battery, LV_ALIGN_TOP_RIGHT, -24, 20);
    }
}

static void build_live(void)
{
    s_live = screen();
    header(s_live, "iPhone Agent", true);
    status_card(s_live, PHONE_Y, LV_SYMBOL_BLUETOOTH "  Phone", &s_phone_state, &s_phone_sub);
    status_card(s_live, LINK_Y, LV_SYMBOL_SHUFFLE "  Server", &s_link_state, &s_link_sub);

    lv_obj_t *act = card(s_live, ACT_Y, ACT_H);
    s_count = label(act, &lv_font_montserrat_14, COL_MUTED, "Activity");
    for (int i = 0; i < RECENT_ROWS; i++) {
        // 命令在左(超长截断),耗时右对齐,两者互不挤占。
        s_recent_cmd[i] = label(act, &lv_font_montserrat_14, COL_TEXT, "");
        lv_obj_set_pos(s_recent_cmd[i], 0, 16 + i * ROW_H);
        lv_obj_set_width(s_recent_cmd[i], CARD_W - 2 * CARD_PAD - 60);
        lv_label_set_long_mode(s_recent_cmd[i], LV_LABEL_LONG_CLIP);
        s_recent_ms[i] = label(act, &lv_font_montserrat_14, COL_MUTED, "");
        lv_obj_align(s_recent_ms[i], LV_ALIGN_TOP_RIGHT, 0, 16 + i * ROW_H);
    }

    // 暂停横幅盖在活动卡上:最显眼的位置,也是暂停后最不需要看的内容。
    s_paused = lv_obj_create(s_live);
    lv_obj_set_pos(s_paused, MARGIN, ACT_Y);
    lv_obj_set_size(s_paused, CARD_W, ACT_H);
    lv_obj_set_style_bg_color(s_paused, lv_color_hex(COL_RED), 0);
    lv_obj_set_style_border_width(s_paused, 0, 0);
    lv_obj_set_style_radius(s_paused, 12, 0);
    lv_obj_clear_flag(s_paused, LV_OBJ_FLAG_SCROLLABLE);
    lv_obj_t *p = label(s_paused, &lv_font_montserrat_20, 0xFFFFFF, LV_SYMBOL_PAUSE "  PAUSED");
    lv_obj_align(p, LV_ALIGN_TOP_MID, 0, 6);
    lv_obj_t *ph = label(s_paused, &lv_font_montserrat_14, 0xFFFFFF, "Agent is blocked.\nPress OK to resume.");
    lv_obj_set_style_text_align(ph, LV_TEXT_ALIGN_CENTER, 0);
    lv_obj_align(ph, LV_ALIGN_BOTTOM_MID, 0, -4);
    lv_obj_add_flag(s_paused, LV_OBJ_FLAG_HIDDEN);

    lv_obj_t *foot = label(s_live, &lv_font_montserrat_14, COL_MUTED, "OK pause    " LV_SYMBOL_DOWN " setup");
    lv_obj_align(foot, LV_ALIGN_BOTTOM_MID, 0, -8);
}

static void build_setup(void)
{
    s_setup = screen();
    header(s_setup, "Setup", false);
    lv_obj_t *c = card(s_setup, PHONE_Y, ACT_Y + ACT_H - PHONE_Y);
    char text[420];
    snprintf(text, sizeof(text),
             "1. iPhone: Settings > Accessibility > Touch > AssistiveTouch: On\n\n"
             "2. iPhone: Settings > Bluetooth > pair \"%s\"\n\n"
             "3. Computer: start the agent server, then\n   python3 tools/usb_link.py\n\n"
             "4. Mirror the screen to \"iPhone Agent\" and calibrate in the web UI",
             s_ble_name);
    lv_obj_t *l = label(c, &lv_font_montserrat_14, COL_TEXT, text);
    lv_obj_set_width(l, CARD_W - 2 * CARD_PAD);
    lv_label_set_long_mode(l, LV_LABEL_LONG_WRAP);

    lv_obj_t *foot = label(s_setup, &lv_font_montserrat_14, COL_MUTED,
                           LV_SYMBOL_UP " back    hold " LV_SYMBOL_UP " forget phone");
    lv_obj_align(foot, LV_ALIGN_BOTTOM_MID, 0, -8);
}

static void set_state(lv_obj_t *l, uint32_t color, const char *text)
{
    lv_obj_set_style_text_color(l, lv_color_hex(color), 0);
    lv_label_set_text(l, text);
}

static void refresh_phone(agent_hid_state_t hid)
{
    switch (hid) {
    case AGENT_HID_READY:
        set_state(s_phone_state, COL_GREEN, "Connected");
        lv_label_set_text(s_phone_sub, "Bluetooth mouse + keyboard");
        break;
    case AGENT_HID_CONNECTED:
        set_state(s_phone_state, COL_BLUE, "Pairing...");
        lv_label_set_text(s_phone_sub, "Tap Pair on the iPhone");
        break;
    case AGENT_HID_ADVERTISING:
        set_state(s_phone_state, COL_AMBER, "Waiting for iPhone");
        lv_label_set_text_fmt(s_phone_sub, "Pair \"%s\" in Bluetooth", s_ble_name);
        break;
    default:
        set_state(s_phone_state, COL_RED, "Bluetooth off");
        lv_label_set_text(s_phone_sub, "BLE failed to start, see log");
        break;
    }
}

static void refresh_link(const agent_snapshot_t *s)
{
    if (s->link[AGENT_LINK_USB] == AGENT_LINK_UP) {
        set_state(s_link_state, COL_GREEN, LV_SYMBOL_USB "  USB linked");
        lv_label_set_text(s_link_sub, "Agent server online");
    } else if (s->link[AGENT_LINK_WIFI] == AGENT_LINK_UP) {
        set_state(s_link_state, COL_GREEN, LV_SYMBOL_WIFI "  Wi-Fi linked");
        lv_label_set_text(s_link_sub, s->wifi_detail);
    } else if (s->link[AGENT_LINK_WIFI] == AGENT_LINK_WAITING) {
        set_state(s_link_state, COL_AMBER, "Waiting for server");
        lv_label_set_text(s_link_sub, s->wifi_detail);
    } else {
        set_state(s_link_state, COL_AMBER, "Waiting for server");
        lv_label_set_text(s_link_sub, s_wifi_configured ? "Wi-Fi not started" : "Run tools/usb_link.py");
    }
}

static void refresh_activity(const agent_snapshot_t *s)
{
    if (s->errors) {
        lv_label_set_text_fmt(s_count, "Activity   %lu cmds, %lu err",
                              (unsigned long)s->commands, (unsigned long)s->errors);
    } else {
        lv_label_set_text_fmt(s_count, "Activity   %lu cmds", (unsigned long)s->commands);
    }
    for (int i = 0; i < RECENT_ROWS; i++) {
        const agent_recent_t *r = &s->recent[i];
        if (!r->line[0]) {
            lv_label_set_text(s_recent_cmd[i], i == 0 ? "No commands yet" : "");
            lv_obj_set_style_text_color(s_recent_cmd[i], lv_color_hex(COL_MUTED), 0);
            lv_label_set_text(s_recent_ms[i], "");
            continue;
        }
        lv_label_set_text_fmt(s_recent_cmd[i], "%s %s", r->ok ? LV_SYMBOL_OK : LV_SYMBOL_CLOSE, r->line);
        lv_obj_set_style_text_color(s_recent_cmd[i],
                                    lv_color_hex(!r->ok ? COL_RED : i == 0 ? COL_TEXT : COL_MUTED), 0);
        lv_label_set_text_fmt(s_recent_ms[i], "%u ms", r->ms);
    }
    if (s->paused) {
        lv_obj_clear_flag(s_paused, LV_OBJ_FLAG_HIDDEN);
    } else {
        lv_obj_add_flag(s_paused, LV_OBJ_FLAG_HIDDEN);
    }
}

static void refresh_battery(void)
{
    int soc = s_soc;
    if (soc < 0) {
        lv_label_set_text(s_battery, "");  // 读不到电量就不画数字
        return;
    }
    const char *icon = soc > 80 ? LV_SYMBOL_BATTERY_FULL : soc > 55 ? LV_SYMBOL_BATTERY_3
                     : soc > 30 ? LV_SYMBOL_BATTERY_2 : soc > 10 ? LV_SYMBOL_BATTERY_1
                     : LV_SYMBOL_BATTERY_EMPTY;
    lv_label_set_text_fmt(s_battery, "%d%% %s", soc, icon);
    lv_obj_set_style_text_color(s_battery, lv_color_hex(soc <= 10 ? COL_RED : COL_MUTED), 0);
}

static void tick(lv_timer_t *t)
{
    (void)t;
    agent_snapshot_t snap;
    agent_bridge_snapshot(&snap);
    agent_hid_state_t hid = agent_hid_state();
    if (snap.change_seq != s_seen_seq || hid != s_seen_hid) {
        // 配对、链路、暂停的变化值得看一眼:点亮屏幕。命令本身不点亮,
        // 否则 agent 运行时屏幕永远不会暗下去。
        s_seen_seq = snap.change_seq;
        s_seen_hid = hid;
        agent_ui_wake();
    }
    refresh_phone(hid);
    refresh_link(&snap);
    refresh_activity(&snap);
    refresh_battery();

    bool dim = esp_timer_get_time() - s_wake_at > DIM_AFTER_US;
    if (dim != s_dim) {
        s_dim = dim;
        bsp_display_backlight(dim ? DIM_PERCENT : BRIGHT_PERCENT);
    }
}

void agent_ui_start(const char *ble_name, bool wifi_configured)
{
    s_ble_name = ble_name;
    s_wifi_configured = wifi_configured;
    build_live();
    build_setup();
    agent_ui_wake();
    tick(NULL);
    lv_screen_load(s_live);
    s_timer = lv_timer_create(tick, REFRESH_MS, NULL);
}

void agent_ui_show_next(void)
{
    lv_screen_load(lv_screen_active() == s_live ? s_setup : s_live);
}

void agent_ui_wake(void)
{
    s_wake_at = esp_timer_get_time();
}

void agent_ui_set_battery(int percent)
{
    s_soc = percent;
}
