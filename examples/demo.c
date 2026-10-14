#include "ducktape_laya.h"
#include "demo_form.h"
#include <stdio.h>
#include <stdlib.h>

int main(int argc, char **argv) {
    const char *model = argc > 1 ? argv[1] : getenv("LAYA_MODEL");
    if (!model) { fputs("Use ./demo c or supply a model path.\n", stderr); return 1; }
    char *request = demo_form();
    if (!request) return 1;
    dtl_engine *engine = NULL;
    char *result = NULL, *error = NULL;
    puts("Running Laya...");
    dtl_status status = dtl_open(model, NULL, &engine, &error);
    if (!status) status = dtl_decide(engine, request, &result, &error);
    int failed = status != DTL_OK;
    if (failed) fprintf(stderr, "Error: %s\n", error ? error : "Laya failed");
    else failed = demo_print(result);
    dtl_free(error);
    dtl_free(result);
    dtl_close(engine);
    free(request);
    return failed;
}
