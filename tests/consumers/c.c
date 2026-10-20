// Public-API conformance consumer; no demo dependencies.
#include "ducktape_laya.h"
#include <stdio.h>
#include <stdlib.h>
int main(int argc, char **argv) {
    if (argc != 2 && argc != 3) { fprintf(stderr, "Usage: %s model.gguf [config.json] < request.json\n", argv[0]); return 2; }
    char *input = malloc(1048578);
    if (!input) return 1;
    size_t n = fread(input, 1, 1048577, stdin);
    if (n > 1048576 || ferror(stdin)) { free(input); return 2; }
    input[n] = 0;
    dtl_engine *engine = NULL;
    char *error = NULL, *result = NULL;
    dtl_status status = dtl_open(argv[1], argc == 3 ? argv[2] : "{\"threads\":4}", &engine, &error);
    if (!status) status = dtl_execute(engine, input, NULL, &result, &error);
    if (status) fprintf(stderr, "%s\n", error ? error : "Laya failed");
    else puts(result);
    dtl_free(error); dtl_free(result); dtl_close(engine); free(input);
    return status ? 1 : 0;
}
