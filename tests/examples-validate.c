/* Model-free request check for tests/examples-smoke.py. Opens a router whose only model
 * is a file that is not a model (loading is lazy), executes the request on stdin, and
 * prints "valid" when the request passed validation (execution then fails loading the
 * model), "invalid: MESSAGE" when validation rejected it. */
#include "ducktape_laya.h"
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
int main(int argc, char **argv) {
    if (argc != 2) { fprintf(stderr, "Usage: %s ROUTER_JSON < request.json\n", argv[0]); return 2; }
    char *input = malloc(1048578);
    if (!input) return 1;
    size_t n = fread(input, 1, 1048577, stdin);
    if (n > 1048576 || ferror(stdin)) { free(input); return 2; }
    input[n] = 0;
    dtl_engine *engine = NULL;
    char *error = NULL, *result = NULL;
    if (dtl_open_router(argv[1], NULL, &engine, &error)) { fprintf(stderr, "router: %s\n", error ? error : "failed"); return 1; }
    dtl_status status = dtl_execute(engine, input, NULL, &result, &error);
    if (status == DTL_INVALID_ARGUMENT) printf("invalid: %s\n", error ? error : "");
    else puts(status ? "valid" : "valid (executed)");
    dtl_free(error); dtl_free(result); dtl_close(engine); free(input);
    return 0;
}
