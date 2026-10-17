/* Reports runtime diagnostics and the dtl_open status as one JSON line. */
#include "ducktape_laya.h"
#include <stdio.h>

int main(int argc, char **argv) {
    if (argc != 2) { fprintf(stderr, "Usage: %s model.gguf\n", argv[0]); return 2; }
    char *info = NULL, *error = NULL;
    if (dtl_info(NULL, &info, &error)) { fprintf(stderr, "%s\n", error ? error : "dtl_info failed"); return 3; }
    dtl_options options = dtl_default_options();
    dtl_engine *engine = NULL;
    dtl_status status = dtl_open(argv[1], &options, &engine, &error);
    printf("{\"status\":%d,\"info\":%s}\n", (int)status, info);
    if (error) fprintf(stderr, "%s\n", error);
    dtl_free(info);
    dtl_free(error);
    dtl_close(engine);
    return 0;
}
