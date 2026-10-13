// Host tests for main/agent_text.c: truncation never splits a UTF-8 character.
#include <assert.h>
#include <stdio.h>
#include <string.h>

#include "agent_text.h"

int main(void)
{
    char buf[8];

    // Fits: copied unchanged.
    assert(agent_utf8_copy(buf, sizeof(buf), "ping") == 4);
    assert(strcmp(buf, "ping") == 0);

    // ASCII longer than the buffer: cut at size - 1 bytes.
    assert(agent_utf8_copy(buf, sizeof(buf), "move 100 200") == 7);
    assert(strcmp(buf, "move 10") == 0);

    // "type 你好": 5 ASCII bytes + two 3-byte characters. Seven bytes would end inside 你,
    // so the cut moves back to before it.
    assert(agent_utf8_copy(buf, sizeof(buf), "type \xE4\xBD\xA0\xE5\xA5\xBD") == 5);
    assert(strcmp(buf, "type ") == 0);

    // A cut that falls exactly on a character boundary keeps the whole character.
    char nine[9];
    assert(agent_utf8_copy(nine, sizeof(nine), "type \xE4\xBD\xA0\xE5\xA5\xBD") == 8);
    assert(strcmp(nine, "type \xE4\xBD\xA0") == 0);

    // 4-byte character (emoji) is kept whole or dropped whole.
    assert(agent_utf8_copy(buf, sizeof(buf), "ab\xF0\x9F\x98\x80" "cd") == 7);
    assert(strcmp(buf, "ab\xF0\x9F\x98\x80" "c") == 0);
    char five[5];
    assert(agent_utf8_copy(five, sizeof(five), "ab\xF0\x9F\x98\x80") == 2);
    assert(strcmp(five, "ab") == 0);

    // Degenerate sizes.
    char one[1] = {'x'};
    assert(agent_utf8_copy(one, sizeof(one), "abc") == 0 && one[0] == '\0');
    assert(agent_utf8_copy(buf, 0, "abc") == 0);

    printf("agent_text: all tests passed\n");
    return 0;
}
