#include "agent_keymap.h"

#include <string.h>

// HID Usage Page 0x07(Keyboard/Keypad)中用到的键码。
#define KEY_A         0x04
#define KEY_1         0x1E
#define KEY_0         0x27
#define KEY_ENTER     0x28
#define KEY_ESC       0x29
#define KEY_BACKSPACE 0x2A
#define KEY_TAB       0x2B
#define KEY_SPACE     0x2C
#define KEY_DELETE    0x4C
#define KEY_RIGHT     0x4F
#define KEY_LEFT      0x50
#define KEY_DOWN      0x51
#define KEY_UP        0x52

// 美式布局的符号键:未按 Shift 时的字符、按 Shift 时的字符、键码。
static const struct {
    char plain;
    char shifted;
    uint8_t key;
} SYMBOLS[] = {
    { '-', '_', 0x2D }, { '=', '+', 0x2E }, { '[', '{', 0x2F }, { ']', '}', 0x30 },
    { '\\', '|', 0x31 }, { ';', ':', 0x33 }, { '\'', '"', 0x34 }, { '`', '~', 0x35 },
    { ',', '<', 0x36 }, { '.', '>', 0x37 }, { '/', '?', 0x38 },
};

// 数字键行按 Shift 得到的符号,下标对应 '1'..'9','0'。
static const char DIGIT_SHIFTED[] = "!@#$%^&*()";

bool agent_keymap_ascii(char c, uint8_t *key, uint8_t *mods)
{
    *mods = 0;
    if (c >= 'a' && c <= 'z') {
        *key = (uint8_t)(KEY_A + (c - 'a'));
        return true;
    }
    if (c >= 'A' && c <= 'Z') {
        *key = (uint8_t)(KEY_A + (c - 'A'));
        *mods = AGENT_MOD_SHIFT;
        return true;
    }
    if (c >= '1' && c <= '9') {
        *key = (uint8_t)(KEY_1 + (c - '1'));
        return true;
    }
    if (c == '0') {
        *key = KEY_0;
        return true;
    }
    switch (c) {
    case ' ':  *key = KEY_SPACE; return true;
    case '\n': *key = KEY_ENTER; return true;
    case '\t': *key = KEY_TAB; return true;
    default: break;
    }
    for (int i = 0; i < 10; i++) {
        if (c == DIGIT_SHIFTED[i]) {
            *key = (uint8_t)(i == 9 ? KEY_0 : KEY_1 + i);
            *mods = AGENT_MOD_SHIFT;
            return true;
        }
    }
    for (size_t i = 0; i < sizeof(SYMBOLS) / sizeof(SYMBOLS[0]); i++) {
        if (c == SYMBOLS[i].plain || c == SYMBOLS[i].shifted) {
            *key = SYMBOLS[i].key;
            *mods = c == SYMBOLS[i].shifted ? AGENT_MOD_SHIFT : 0;
            return true;
        }
    }
    return false;
}

static const struct {
    const char *name;
    uint8_t mod;
    uint8_t key;
} NAMED[] = {
    { "cmd", AGENT_MOD_GUI, 0 },   { "ctrl", AGENT_MOD_CTRL, 0 },
    { "alt", AGENT_MOD_ALT, 0 },   { "shift", AGENT_MOD_SHIFT, 0 },
    { "enter", 0, KEY_ENTER },     { "esc", 0, KEY_ESC },
    { "tab", 0, KEY_TAB },         { "backspace", 0, KEY_BACKSPACE },
    { "delete", 0, KEY_DELETE },   { "space", 0, KEY_SPACE },
    { "up", 0, KEY_UP },           { "down", 0, KEY_DOWN },
    { "left", 0, KEY_LEFT },       { "right", 0, KEY_RIGHT },
};

static bool add_token(const char *tok, size_t len, agent_combo_t *out)
{
    if (len == 0) return false;
    uint8_t key = 0, mods = 0;
    bool found = false;
    for (size_t i = 0; i < sizeof(NAMED) / sizeof(NAMED[0]); i++) {
        if (strlen(NAMED[i].name) == len && memcmp(NAMED[i].name, tok, len) == 0) {
            mods = NAMED[i].mod;
            key = NAMED[i].key;
            found = true;
            break;
        }
    }
    if (!found && !(len == 1 && agent_keymap_ascii(tok[0], &key, &mods))) return false;
    out->mods |= mods;
    if (key) {
        if (out->key_count >= AGENT_KEYS_MAX) return false;
        out->keys[out->key_count++] = key;
    }
    return true;
}

bool agent_keymap_combo(const char *text, agent_combo_t *out)
{
    memset(out, 0, sizeof(*out));
    if (!text || !*text) return false;
    const char *start = text;
    for (const char *p = text;; p++) {
        if (*p == '+' || *p == '\0') {
            if (!add_token(start, (size_t)(p - start), out)) return false;
            if (*p == '\0') break;
            start = p + 1;
        }
    }
    return true;
}
