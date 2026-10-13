#include "agent_text.h"

#include <string.h>

size_t agent_utf8_copy(char *dst, size_t size, const char *src)
{
    if (size == 0) return 0;
    size_t n = strlen(src);
    if (n >= size) {
        n = size - 1;
        // 截断点落在多字节字符中间(下一个字节是续字节 10xxxxxx)时,退回到该字符的首字节。
        while (n > 0 && ((unsigned char)src[n] & 0xC0) == 0x80) n--;
    }
    memcpy(dst, src, n);
    dst[n] = '\0';
    return n;
}
