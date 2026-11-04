/* Real vendor mapping macros, with kernel printk prefixes captured in userspace. */
#include <assert.h>
#include <stdarg.h>
#include <stdio.h>
#include <string.h>
#define KERNEL_VERSION(a,b,c) (((a) << 16) + ((b) << 8) + (c))
#define LINUX_VERSION_CODE KERNEL_VERSION(6,12,112)
#define DBG_ENHANCE
#define KERN_ERR "\0013"
#define KERN_WARNING "\0014"
#define KERN_INFO "\0016"
static char output[32];
static void capture(const char *format, ...)
{
    va_list args;
    va_start(args, format);
    vsnprintf(output, sizeof(output), format, args);
    va_end(args);
}
#define pr_err(fmt, ...) capture(KERN_ERR fmt, __VA_ARGS__)
#define pr_warn(fmt, ...) capture(KERN_WARNING fmt, __VA_ARGS__)
#define pr_info(fmt, ...) capture(KERN_INFO fmt, __VA_ARGS__)
#include "mapping.h"
int main(void)
{
    MTWF_PRINT_DBG_LVL_ERROR("%s", "error");
    assert(!strcmp(output, "\0013error"));
    MTWF_PRINT_DBG_LVL_WARN("%s", "warning");
    assert(!strcmp(output, "\0014warning"));
    MTWF_PRINT_DBG_LVL_INFO("%s", "info");
    assert(!strcmp(output, "\0016info"));
    puts("KERN_ERR=3 KERN_WARNING=4 KERN_INFO=6");
    return 0;
}
