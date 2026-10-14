#include "agent_exec.h"

#include "agent_keymap.h"

#include <ctype.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#define ERR_NOT_READY "err phone not connected (pair AI Passport in iPhone Bluetooth settings)"
#define ERR_LOST      "err phone disconnected"
#define ERR_PAUSED    "err paused on AI Passport (press OK to resume)"

void agent_exec_init(agent_exec_t *ex, const agent_hid_ops_t *ops, const agent_exec_cfg_t *cfg)
{
    memset(ex, 0, sizeof(*ex));
    ex->ops = *ops;
    ex->cfg = *cfg;
    if (ex->cfg.step < 1) ex->cfg.step = 1;
    if (ex->cfg.step > 127) ex->cfg.step = 127;
}

static int clamp(long v, int lim)
{
    return v > lim ? lim : v < -lim ? -lim : (int)v;
}

// 报告发送失败的两种原因:手机断开,或用户在执行中途按了暂停。
static bool send_mouse(agent_exec_t *ex, int dx, int dy, int wheel, int pan)
{
    const uint8_t r[AGENT_MOUSE_REPORT_LEN] = {
        ex->buttons, (uint8_t)(int8_t)dx, (uint8_t)(int8_t)dy,
        (uint8_t)(int8_t)wheel, (uint8_t)(int8_t)pan,
    };
    return ex->ops.mouse(ex->ops.ctx, r);
}

static bool send_keyboard(agent_exec_t *ex, uint8_t mods, const uint8_t *keys, int count)
{
    uint8_t r[AGENT_KEY_REPORT_LEN] = { mods };
    for (int i = 0; i < count && i < AGENT_KEYS_MAX; i++) r[2 + i] = keys[i];
    return ex->ops.keyboard(ex->ops.ctx, r);
}

static bool mouse(agent_exec_t *ex, int dx, int dy, int wheel, int pan)
{
    return !ex->paused && send_mouse(ex, dx, dy, wheel, pan);
}

static bool keyboard(agent_exec_t *ex, uint8_t mods, const uint8_t *keys, int count)
{
    return !ex->paused && send_keyboard(ex, mods, keys, count);
}

static void pace(agent_exec_t *ex, uint32_t ms)
{
    ex->ops.sleep_ms(ex->ops.ctx, ms > ex->cfg.interval_ms ? ms : ex->cfg.interval_ms);
}

// d 的 i/n,四舍五入;逐步取差,累计正好是 d。
static long share(long d, long i, long n)
{
    return (2 * d * i + (d < 0 ? -n : n)) / (2 * n);
}

// 以恒定速度沿直线分步移动:iOS 的指针加速取决于速度,所以按合成位移(而非每轴)限制每个报告,
// 任何方向的速度都相同,服务器的一次标定才对所有方向都成立。
static bool move_paced(agent_exec_t *ex, long dx, long dy)
{
    const long step = ex->cfg.step, len2 = dx * dx + dy * dy;
    long n = (labs(dx) > labs(dy) ? labs(dx) : labs(dy)) / step;
    while (n * n * step * step < len2) n++;  // 最少步数,使每步合成位移不超过 step
    long px = 0, py = 0;
    for (long i = 1; i <= n; i++) {
        long x = share(dx, i, n), y = share(dy, i, n);
        if (!mouse(ex, (int)(x - px), (int)(y - py), 0, 0)) return false;
        px = x;
        py = y;
        pace(ex, 0);
    }
    return true;
}

static bool set_buttons(agent_exec_t *ex, uint8_t buttons)
{
    ex->buttons = buttons;
    return mouse(ex, 0, 0, 0, 0);
}

static bool type_text(agent_exec_t *ex, const char *text)
{
    for (const char *p = text; *p; p++) {
        uint8_t key, mods;
        // 与参考固件(Arduino Keyboard.print)一致:无法映射的字符静默跳过。
        if (!agent_keymap_ascii(*p, &key, &mods)) continue;
        if (!keyboard(ex, mods, &key, 1)) return false;
        pace(ex, 0);
        if (!keyboard(ex, 0, NULL, 0)) return false;
        pace(ex, 0);
    }
    return true;
}

static void parse_two(const char *arg, long *a, long *b)
{
    char *end;
    *a = strtol(arg, &end, 10);
    *b = strtol(end, NULL, 10);
}

bool agent_exec_run(agent_exec_t *ex, const char *line, char *reply, size_t reply_len)
{
    const char *sp = strchr(line, ' ');
    size_t cmd_len = sp ? (size_t)(sp - line) : strlen(line);
    const char *arg = sp ? sp + 1 : "";
    char cmd[12] = "";
    if (cmd_len < sizeof(cmd)) {
        memcpy(cmd, line, cmd_len);
        cmd[cmd_len] = '\0';
    }

#define REPLY(text) do { snprintf(reply, reply_len, "%s", text); return strcmp(text, "ok") == 0; } while (0)
#define SEND(expr) do { if (!(expr)) REPLY(ex->paused ? ERR_PAUSED : ERR_LOST); } while (0)

    if (strcmp(cmd, "ping") == 0) REPLY("ok");
    if (ex->paused) REPLY(ERR_PAUSED);
    if (!ex->ops.ready(ex->ops.ctx)) REPLY(ERR_NOT_READY);

    long a = 0, b = 0;
    if (strcmp(cmd, "home") == 0) {  // 把指针推进左上角:已知位置
        for (int i = 0; i < ex->cfg.home_reports; i++) {
            SEND(mouse(ex, -127, -127, 0, 0));
            pace(ex, 0);
        }
        REPLY("ok");
    }
    if (strcmp(cmd, "move") == 0) {
        parse_two(arg, &a, &b);
        if (labs(a) > ex->cfg.max_move || labs(b) > ex->cfg.max_move) REPLY("err move too large");
        SEND(move_paced(ex, a, b));
        REPLY("ok");
    }
    if (strcmp(cmd, "press") == 0) {
        SEND(set_buttons(ex, ex->buttons | AGENT_MOUSE_LEFT));
        pace(ex, 0);
        REPLY("ok");
    }
    if (strcmp(cmd, "release") == 0) {
        SEND(set_buttons(ex, ex->buttons & (uint8_t)~AGENT_MOUSE_LEFT));
        pace(ex, 0);
        REPLY("ok");
    }
    if (strcmp(cmd, "click") == 0) {
        SEND(set_buttons(ex, ex->buttons | AGENT_MOUSE_LEFT));
        pace(ex, ex->cfg.click_ms);
        SEND(set_buttons(ex, ex->buttons & (uint8_t)~AGENT_MOUSE_LEFT));
        pace(ex, 0);
        REPLY("ok");
    }
    if (strcmp(cmd, "scroll") == 0) {
        parse_two(arg, &a, &b);
        SEND(mouse(ex, 0, 0, clamp(a, 127), clamp(b, 127)));
        pace(ex, 0);
        REPLY("ok");
    }
    if (strcmp(cmd, "type") == 0) {
        SEND(type_text(ex, arg));
        REPLY("ok");
    }
    if (strcmp(cmd, "key") == 0) {
        agent_combo_t combo;
        // 去掉服务器可能带来的首尾空白,再按 '+' 分段。
        char buf[64];
        while (isspace((unsigned char)*arg)) arg++;
        snprintf(buf, sizeof(buf), "%s", arg);
        for (size_t n = strlen(buf); n && isspace((unsigned char)buf[n - 1]); n--) buf[n - 1] = '\0';
        if (!agent_keymap_combo(buf, &combo)) REPLY("err unknown key");
        SEND(keyboard(ex, combo.mods, combo.keys, combo.key_count));
        pace(ex, ex->cfg.key_ms);
        SEND(keyboard(ex, 0, NULL, 0));
        pace(ex, 0);
        REPLY("ok");
    }
    REPLY("err unknown command");
#undef SEND
#undef REPLY
}

void agent_exec_release_all(agent_exec_t *ex)
{
    ex->buttons = 0;
    if (!ex->ops.ready(ex->ops.ctx)) return;
    (void)send_mouse(ex, 0, 0, 0, 0);
    (void)send_keyboard(ex, 0, NULL, 0);
}

void agent_exec_set_paused(agent_exec_t *ex, bool paused)
{
    ex->paused = paused;
    if (paused) agent_exec_release_all(ex);
}
