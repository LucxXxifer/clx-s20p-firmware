/* Execute the unchanged upstream validator with real libubox blobmsg entries. */
#include <assert.h>
#include <stdbool.h>
#include <stdio.h>
#include "blobmsg.h"
#include "validator.inc"

static void check_array(unsigned kind, bool valid)
{
	struct blob_buf b = { 0 };
	void *array, *nested;
	char unterminated[] = { 'a', 'b', 'c' };

	assert(blob_buf_init(&b, 0) == 0);
	array = blobmsg_open_array(&b, "params");
	switch (kind) {
	case 0: break;
	case 1: blobmsg_add_string(&b, NULL, "system"); break;
	case 2: blobmsg_add_u32(&b, NULL, 42); break;
	case 3: blobmsg_add_u8(&b, NULL, true); break;
	case 4:
		nested = blobmsg_open_table(&b, NULL);
		blobmsg_add_string(&b, "path", "system");
		blobmsg_close_table(&b, nested);
		break;
	case 5:
		nested = blobmsg_open_array(&b, NULL);
		blobmsg_add_string(&b, NULL, "system");
		blobmsg_close_array(&b, nested);
		break;
	case 6:
		blobmsg_add_field(&b, BLOBMSG_TYPE_STRING, NULL, unterminated, sizeof(unterminated));
		break;
	case 7:
		blobmsg_add_string(&b, NULL, "system");
		blobmsg_add_u32(&b, NULL, 42);
		break;
	case 8:
		blobmsg_add_string(&b, NULL, "");
		blobmsg_add_string(&b, NULL, "system");
		break;
	default: assert(false);
	}
	blobmsg_close_array(&b, array);
	assert(uh_ubus_valid_list_params(blob_data(b.head)) == valid);
	blob_buf_free(&b);
}

int main(void)
{
	struct blob_buf b = { 0 };
	assert(uh_ubus_valid_list_params(NULL));
	assert(blob_buf_init(&b, 0) == 0);
	blobmsg_add_u32(&b, "params", 42);
	assert(uh_ubus_valid_list_params(blob_data(b.head)));
	blob_buf_free(&b);
	check_array(0, true);
	check_array(1, true);
	check_array(2, false);
	check_array(3, false);
	check_array(4, false);
	check_array(5, false);
	check_array(6, false);
	check_array(7, false);
	check_array(8, true);
	puts("11 real-libubox validator cases passed; no HTTP or target-device claim");
	return 0;
}
