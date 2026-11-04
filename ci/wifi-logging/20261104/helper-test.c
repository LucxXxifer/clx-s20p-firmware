/* Compile the exact helper extracted from the applied driver patch. */
#include <assert.h>
#include <limits.h>
#include <pthread.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>

#define HZ 100UL
#define DEFINE_SPINLOCK(name) pthread_mutex_t name = PTHREAD_MUTEX_INITIALIZER
#define spin_lock_irqsave(lock, flags) do { (flags) = 0; assert(!pthread_mutex_lock(lock)); } while (0)
#define spin_unlock_irqrestore(lock, flags) do { (void)(flags); assert(!pthread_mutex_unlock(lock)); } while (0)
#if TEST_BITS == 32
#define time_after_eq(a, b) ((int32_t)((uint32_t)(a) - (uint32_t)(b)) >= 0)
#define TEST_MAX UINT32_MAX
#else
#define time_after_eq(a, b) ((int64_t)((uint64_t)(a) - (uint64_t)(b)) >= 0)
#define TEST_MAX UINT64_MAX
#endif

#include "helper.c"

static void reset(void)
{
    pmf_action_log_started = false;
    pmf_action_log_next = 0;
    pmf_action_log_suppressed = 0;
}

static void *burst(void *unused)
{
    unsigned int suppressed = 0;
    (void)unused;
    for (int i = 0; i < 10000; i++)
        assert(!pmf_action_log_allow(1, &suppressed));
    return NULL;
}

int main(void)
{
    unsigned int suppressed = 123;
    reset();
    assert(pmf_action_log_allow(0, &suppressed));
    assert(suppressed == 0);
    for (int i = 0; i < 1000; i++)
        assert(!pmf_action_log_allow(0, &suppressed));
    assert(!pmf_action_log_allow(5 * HZ - 1, &suppressed));
    assert(pmf_action_log_allow(5 * HZ, &suppressed));
    assert(suppressed == 1001);
    assert(pmf_action_log_allow(15 * HZ, &suppressed));
    assert(suppressed == 0);

    reset();
    assert(pmf_action_log_allow(TEST_MAX - 2 * HZ, &suppressed));
    assert(!pmf_action_log_allow(TEST_MAX, &suppressed));
    assert(!pmf_action_log_allow(3 * HZ - 2, &suppressed));
    assert(pmf_action_log_allow(3 * HZ - 1, &suppressed));
    assert(suppressed == 2);

    reset();
    assert(pmf_action_log_allow(0, &suppressed));
    pmf_action_log_suppressed = UINT_MAX - 1;
    assert(!pmf_action_log_allow(1, &suppressed));
    assert(!pmf_action_log_allow(1, &suppressed));
    assert(pmf_action_log_suppressed == UINT_MAX);
    assert(pmf_action_log_allow(5 * HZ, &suppressed));
    assert(suppressed == UINT_MAX);
    assert(pmf_action_log_suppressed == 0);

    reset();
    assert(pmf_action_log_allow(0, &suppressed));
    pthread_t threads[8];
    for (int i = 0; i < 8; i++)
        assert(!pthread_create(&threads[i], NULL, burst, NULL));
    for (int i = 0; i < 8; i++)
        assert(!pthread_join(threads[i], NULL));
    assert(pmf_action_log_allow(5 * HZ, &suppressed));
    assert(suppressed == 80000);
    puts("burst rollover saturation concurrency: pass");
    return 0;
}
