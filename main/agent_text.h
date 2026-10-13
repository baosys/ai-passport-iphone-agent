// main/agent_text.h —— 屏幕文字的小工具(纯逻辑,有 host 测试)。
#pragma once

#include <stddef.h>

// 把 src 复制进 dst(容量 size,含结尾 NUL),超长时截断,但只在 UTF-8 字符边界处截断:
// 按字节截断会把中文字切成半个,屏幕上就成了乱码。src 本身的非法字节原样保留。
// 返回写入的字节数(不含 NUL);size 为 0 时什么也不写,返回 0。
size_t agent_utf8_copy(char *dst, size_t size, const char *src);
