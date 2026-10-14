// Host tests for the board-side command executor and key map (main/agent_exec.c,
// main/agent_keymap.c): protocol replies, pacing, button state and key reports.
#include <assert.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>

#include "agent_exec.h"
#include "agent_keymap.h"

#define MAX_REPORTS 512

typedef struct {
    bool ready;
    int fail_after;  // reports accepted before the fake link drops; -1 = never
    int pause_after; // reports sent before the user presses pause; -1 = never
    int mouse_count;
    int key_count;
    uint8_t mouse[MAX_REPORTS][AGENT_MOUSE_REPORT_LEN];
    uint8_t keys[MAX_REPORTS][AGENT_KEY_REPORT_LEN];
    uint32_t slept_ms;
} fake_t;

static fake_t s_fake;
static agent_exec_t s_ex;

// Another task pressing OK sets paused without the lock while a gesture runs.
static void maybe_pause(fake_t *f);

static bool fake_ready(void *ctx) { return ((fake_t *)ctx)->ready; }

static bool fake_alive(fake_t *f)
{
    if (f->fail_after < 0) return true;
    if (f->fail_after == 0) return false;
    f->fail_after--;
    return true;
}

static bool fake_mouse(void *ctx, const uint8_t r[AGENT_MOUSE_REPORT_LEN])
{
    fake_t *f = ctx;
    if (!fake_alive(f)) return false;
    assert(f->mouse_count < MAX_REPORTS);
    memcpy(f->mouse[f->mouse_count++], r, AGENT_MOUSE_REPORT_LEN);
    maybe_pause(f);
    return true;
}

static bool fake_keyboard(void *ctx, const uint8_t r[AGENT_KEY_REPORT_LEN])
{
    fake_t *f = ctx;
    if (!fake_alive(f)) return false;
    assert(f->key_count < MAX_REPORTS);
    memcpy(f->keys[f->key_count++], r, AGENT_KEY_REPORT_LEN);
    return true;
}

static void maybe_pause(fake_t *f)
{
    if (f->pause_after > 0 && --f->pause_after == 0) s_ex.paused = true;
}

static void fake_sleep(void *ctx, uint32_t ms) { ((fake_t *)ctx)->slept_ms += ms; }

static char s_reply[128];

static void reset(void)
{
    memset(&s_fake, 0, sizeof(s_fake));
    s_fake.ready = true;
    s_fake.fail_after = -1;
    s_fake.pause_after = -1;
    const agent_hid_ops_t ops = {
        .ready = fake_ready, .mouse = fake_mouse, .keyboard = fake_keyboard,
        .sleep_ms = fake_sleep, .ctx = &s_fake,
    };
    const agent_exec_cfg_t cfg = AGENT_EXEC_CFG_DEFAULT();
    agent_exec_init(&s_ex, &ops, &cfg);
}

static bool run(const char *line)
{
    return agent_exec_run(&s_ex, line, s_reply, sizeof(s_reply));
}

static void test_keymap(void)
{
    uint8_t key, mods;
    assert(agent_keymap_ascii('a', &key, &mods) && key == 0x04 && mods == 0);
    assert(agent_keymap_ascii('Z', &key, &mods) && key == 0x1D && mods == AGENT_MOD_SHIFT);
    assert(agent_keymap_ascii('1', &key, &mods) && key == 0x1E && mods == 0);
    assert(agent_keymap_ascii('0', &key, &mods) && key == 0x27 && mods == 0);
    assert(agent_keymap_ascii('!', &key, &mods) && key == 0x1E && mods == AGENT_MOD_SHIFT);
    assert(agent_keymap_ascii(')', &key, &mods) && key == 0x27 && mods == AGENT_MOD_SHIFT);
    assert(agent_keymap_ascii('@', &key, &mods) && key == 0x1F && mods == AGENT_MOD_SHIFT);
    assert(agent_keymap_ascii('.', &key, &mods) && key == 0x37 && mods == 0);
    assert(agent_keymap_ascii('?', &key, &mods) && key == 0x38 && mods == AGENT_MOD_SHIFT);
    assert(agent_keymap_ascii('"', &key, &mods) && key == 0x34 && mods == AGENT_MOD_SHIFT);
    assert(agent_keymap_ascii(' ', &key, &mods) && key == 0x2C);
    assert(!agent_keymap_ascii('\x01', &key, &mods));
    assert(!agent_keymap_ascii((char)0xE4, &key, &mods));

    agent_combo_t c;
    assert(agent_keymap_combo("cmd+h", &c));
    assert(c.mods == AGENT_MOD_GUI && c.key_count == 1 && c.keys[0] == 0x0B);
    assert(agent_keymap_combo("cmd+space", &c));
    assert(c.mods == AGENT_MOD_GUI && c.key_count == 1 && c.keys[0] == 0x2C);
    assert(agent_keymap_combo("enter", &c) && c.mods == 0 && c.keys[0] == 0x28);
    assert(agent_keymap_combo("cmd+shift+3", &c));
    assert(c.mods == (AGENT_MOD_GUI | AGENT_MOD_SHIFT) && c.keys[0] == 0x20);
    assert(agent_keymap_combo("shift", &c) && c.mods == AGENT_MOD_SHIFT && c.key_count == 0);
    assert(agent_keymap_combo("cmd+A", &c) && c.mods == (AGENT_MOD_GUI | AGENT_MOD_SHIFT));
    assert(!agent_keymap_combo("", &c));
    assert(!agent_keymap_combo("cmd+", &c));
    assert(!agent_keymap_combo("hyper+h", &c));
    assert(!agent_keymap_combo("a+b+c+d+e+f+g", &c));
}

static void test_ping_and_errors(void)
{
    reset();
    assert(run("ping") && strcmp(s_reply, "ok") == 0);
    assert(!run("jump") && strcmp(s_reply, "err unknown command") == 0);
    assert(!run("averyveryverylongcommand 1 2") && strcmp(s_reply, "err unknown command") == 0);
    assert(!run("key hyper+x") && strcmp(s_reply, "err unknown key") == 0);
    assert(!run("move 20000 0") && strcmp(s_reply, "err move too large") == 0);
    assert(s_fake.mouse_count == 0 && s_fake.key_count == 0);

    // A phone that is not paired: ping still answers, everything else is refused at once.
    s_fake.ready = false;
    assert(run("ping"));
    assert(!run("click") && strncmp(s_reply, "err phone not connected", 23) == 0);
    assert(s_fake.mouse_count == 0);
}

static void test_move_is_paced(void)
{
    reset();
    assert(run("move 25 -3"));  // split evenly along the line, not 10 per axis then the rest
    assert(s_fake.mouse_count == 3);
    assert((int8_t)s_fake.mouse[0][1] == 8 && (int8_t)s_fake.mouse[0][2] == -1);
    assert((int8_t)s_fake.mouse[1][1] == 9 && (int8_t)s_fake.mouse[1][2] == -1);
    assert((int8_t)s_fake.mouse[2][1] == 8 && (int8_t)s_fake.mouse[2][2] == -1);
    assert(s_fake.slept_ms == 3 * 15);

    // A diagonal is no faster than a straight move: every report stays within the step length, so iOS
    // pointer acceleration (which depends on speed) is the same in every direction.
    const long moves[][2] = { { 100, 100 }, { 0, 137 }, { -227, 836 }, { 300, -40 }, { 7, 0 } };
    for (size_t m = 0; m < sizeof(moves) / sizeof(moves[0]); m++) {
        char cmd[32];
        snprintf(cmd, sizeof(cmd), "move %ld %ld", moves[m][0], moves[m][1]);
        reset();
        assert(run(cmd));
        long sx = 0, sy = 0;
        for (int i = 0; i < s_fake.mouse_count; i++) {
            int x = (int8_t)s_fake.mouse[i][1], y = (int8_t)s_fake.mouse[i][2];
            assert(x <= 10 && x >= -10 && y <= 10 && y >= -10);  // never past the step on an axis (int8-safe)
            assert(x * x + y * y <= 11 * 11);  // the step length, plus rounding
            sx += x;
            sy += y;
        }
        assert(sx == moves[m][0] && sy == moves[m][1]);
    }
    reset();
    assert(run("move 100 100") && s_fake.mouse_count == 15);  // 141 units at 10 per report

    reset();
    assert(run("move 0 0") && s_fake.mouse_count == 0);
    assert(run("move") && s_fake.mouse_count == 0);  // missing arguments are 0, as in the reference firmware
}

static void test_home_slams_top_left(void)
{
    reset();
    assert(run("home"));
    assert(s_fake.mouse_count == 50);
    for (int i = 0; i < 50; i++) {
        assert((int8_t)s_fake.mouse[i][1] == -127 && (int8_t)s_fake.mouse[i][2] == -127);
    }
}

static void test_drag_keeps_button_down(void)
{
    reset();
    assert(run("press"));
    assert(run("move 0 15"));
    assert(run("release"));
    assert(s_fake.mouse_count == 4);
    assert(s_fake.mouse[0][0] == AGENT_MOUSE_LEFT);
    assert(s_fake.mouse[1][0] == AGENT_MOUSE_LEFT && s_fake.mouse[2][0] == AGENT_MOUSE_LEFT);
    assert(s_fake.mouse[3][0] == 0);

    reset();
    assert(run("click"));
    assert(s_fake.mouse_count == 2);
    assert(s_fake.mouse[0][0] == AGENT_MOUSE_LEFT && s_fake.mouse[1][0] == 0);
    assert(s_fake.slept_ms >= 60);
}

static void test_scroll_clamps(void)
{
    reset();
    assert(run("scroll -3 400"));
    assert(s_fake.mouse_count == 1);
    assert((int8_t)s_fake.mouse[0][3] == -3 && (int8_t)s_fake.mouse[0][4] == 127);
}

static void test_type_and_key(void)
{
    reset();
    assert(run("type Hi!\xC3\xA9"));  // non-ASCII bytes are skipped, like Keyboard.print
    assert(s_fake.key_count == 6);
    assert(s_fake.keys[0][0] == AGENT_MOD_SHIFT && s_fake.keys[0][2] == 0x0B);
    assert(s_fake.keys[1][0] == 0 && s_fake.keys[1][2] == 0);
    assert(s_fake.keys[2][0] == 0 && s_fake.keys[2][2] == 0x0C);
    assert(s_fake.keys[4][0] == AGENT_MOD_SHIFT && s_fake.keys[4][2] == 0x1E);

    reset();
    assert(run("type hello world"));
    assert(s_fake.key_count == 22);

    reset();
    assert(run("key  cmd+h "));
    assert(s_fake.key_count == 2);
    assert(s_fake.keys[0][0] == AGENT_MOD_GUI && s_fake.keys[0][2] == 0x0B);
    assert(s_fake.keys[1][0] == 0 && s_fake.keys[1][2] == 0);
}

static void test_pause_and_release(void)
{
    reset();
    assert(run("press"));
    agent_exec_set_paused(&s_ex, true);
    // Pausing lets go of the button and the keys at once.
    assert(s_fake.mouse[s_fake.mouse_count - 1][0] == 0);
    assert(s_fake.key_count == 1 && s_fake.keys[0][0] == 0);
    assert(run("ping"));
    assert(!run("move 5 5") && strncmp(s_reply, "err paused", 10) == 0);
    agent_exec_set_paused(&s_ex, false);
    assert(run("move 5 5"));
    assert(s_fake.mouse[s_fake.mouse_count - 1][0] == 0);  // the button stays released after resuming
}

static void test_pause_stops_a_gesture_in_flight(void)
{
    reset();
    assert(run("press"));
    s_fake.pause_after = 3;
    assert(!run("move 0 500") && strncmp(s_reply, "err paused", 10) == 0);
    assert(s_fake.mouse_count == 4);  // press + 3 steps, then nothing more
    agent_exec_set_paused(&s_ex, true);  // what the bridge does next, under the lock
    assert(s_fake.mouse[s_fake.mouse_count - 1][0] == 0);
}

static void test_link_lost_mid_gesture(void)
{
    reset();
    s_fake.fail_after = 2;
    assert(!run("move 100 0") && strcmp(s_reply, "err phone disconnected") == 0);
    assert(s_fake.mouse_count == 2);
}

int main(void)
{
    test_keymap();
    test_ping_and_errors();
    test_move_is_paced();
    test_home_slams_top_left();
    test_drag_keeps_button_down();
    test_scroll_clamps();
    test_type_and_key();
    test_pause_and_release();
    test_pause_stops_a_gesture_in_flight();
    test_link_lost_mid_gesture();
    puts("test_agent_exec: PASS");
    return 0;
}
