/* Compile the exact vendor logger with small userspace OS stubs. */
#include <assert.h>
#include <limits.h>

#include <stdio.h>
#include <stdarg.h>
#include <stdbool.h>
#include <stdint.h>
#include <string.h>
#define IN
#define DBG_PRINT_BUF_SIZE 160
#define MAX_MULTI_STA 2
#define MAX_WDS_ENTRY 2
#define VALID_MBSS(p,i) ((i) < 2)
#define INT_MAIN 0
#define INT_MBSSID 1
#define INT_APCLI 2
#define INT_WDS 3
#define DBG_LVL_OFF 0
#define DBG_LVL_ERROR 1
#define DBG_LVL_WARN 2
#define DBG_LVL_NOTICE 3
#define DBG_LVL_INFO 4
#define DBG_LVL_DEBUG 5
#define DBG_CAT_ALL 0
#define DBG_SUBCAT_ALL 0
#define os_snprintf_error(size,ret) ((ret)<0 || (size_t)(ret)>=(size_t)(size))
#define MTWF_DBG(...) do {} while(0)
typedef uint32_t UINT32;
typedef char INT8;
typedef int INT32;
typedef unsigned int UINT;
struct net_device { char name[16]; };
struct wifi_dev { struct net_device *if_dev; };
struct vif { struct wifi_dev wdev; };
struct os_cookie { unsigned int ioctl_if; unsigned int ioctl_if_type; };
typedef struct os_cookie *POS_COOKIE;
typedef struct { unsigned int ChipID; void *OS_Cookie; void *hdev_ctrl; struct { struct vif MBSSID[2]; } ApCfg; struct vif StaCfg[2]; struct { struct vif WdsEntry[2]; } WdsTab; } RTMP_ADAPTER;
struct task { char comm[16]; } task = {"kworker/0:12345"};
struct task *current=&task;
static bool mtwf_dbg_prtCatLvl=true;
static bool mtwf_dbg_prtIntfName=false;
static bool mtwf_dbg_prtThreadId=false;
static bool mtwf_dbg_prtFuncLine=true;
static unsigned errors;
static char output[160];
static void capture(const char *fmt, ...) { va_list ap; va_start(ap,fmt); vsnprintf(output,sizeof(output),fmt,ap); va_end(ap); }
#define MTWF_PRINT(...) do { errors++; } while(0)
#define MTWF_PRINT_DBG_LVL_OFF capture
#define MTWF_PRINT_DBG_LVL_ERROR capture
#define MTWF_PRINT_DBG_LVL_WARN capture
#define MTWF_PRINT_DBG_LVL_NOTICE capture
#define MTWF_PRINT_DBG_LVL_INFO capture
#define MTWF_PRINT_DBG_LVL_DEBUG capture
#include "logger.c"
#undef MTWF_DBG
#define MTWF_DBG(pAd, Category, SubCategory, Level, ...) \
    mtwf_dbg_prt(pAd, Category, Level, __func__, __LINE__, __VA_ARGS__)
#define DBG_CAT_SEC 13
#define CATSEC_PMF 0
struct frame_control { unsigned int SubType; };
struct rx_block { unsigned int wcid; };

static void PMF_PerformRxFrameAction(RTMP_ADAPTER *pAd, unsigned int suppressed,
                                   unsigned int wcid)
{
    struct frame_control frame = {13};
    struct frame_control *FC = &frame;
    struct rx_block rx = {wcid};
    struct rx_block *pRxBlk = &rx;
#include "diagnostic.h"
}

int main(void)
{
    struct net_device dev = {"wlan01234567890"};
    struct os_cookie cookie = {0, INT_MAIN};
    RTMP_ADAPTER ad = {.ChipID = 0x7981, .OS_Cookie = &cookie, .hdev_ctrl = &ad};
    unsigned int counts[] = {0, UINT_MAX};
    unsigned int wcids[] = {1, 65535};
    ad.ApCfg.MBSSID[0].wdev.if_dev = &dev;

    for (unsigned int flags = 0; flags < 2; flags++) {
        mtwf_dbg_prtIntfName = flags != 0;
        mtwf_dbg_prtThreadId = flags != 0;
        for (unsigned int count = 0; count < 2; count++) {
            for (unsigned int wcid = 0; wcid < 2; wcid++) {
                char expected[32];
                errors = 0;
                output[0] = '\0';
                PMF_PerformRxFrameAction(&ad, counts[count], wcids[wcid]);
                /* A truncation emits an additional MTWF_PRINT error in the real logger. */
                assert(errors == 0);
                snprintf(expected, sizeof(expected), "suppressed=%u%s", counts[count], count ? "+" : "");
                assert(strstr(output, expected));
                snprintf(expected, sizeof(expected), "WCID=%u", wcids[wcid]);
                assert(strstr(output, expected));
                assert(strstr(output, "PMF"));
                assert(strstr(output, "NR_UCAST"));
                assert(strstr(output, "global"));
                assert(strstr(output, "SubType=13"));
                assert(strstr(output, "PMF_PerformRxFrameAction() 153"));
                assert(strlen(output) < DBG_PRINT_BUF_SIZE);
                assert(strchr(output, '\n') == output + strlen(output) - 1);
                if (flags) {
                    assert(strstr(output, dev.name));
                    assert(strstr(output, current->comm));
                }
            }
        }
    }
    puts("real logger160 default/max-prefix context count newline: pass");
    return 0;
}
