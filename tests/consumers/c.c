// Public-API conformance consumer; no demo dependencies.
// Usage: laya-conformance-c model.gguf [config.json] < request.json
//    or: laya-conformance-c model.gguf [config.json] --requests FILE... [--fresh-engine] (one result line per file)
#include "ducktape_laya.h"
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
// Reads at most 1 MiB into input (1048578 bytes); 0 on success, 2 when too large or unreadable.
static int read_request(FILE *file, char *input) {
    size_t n = fread(input, 1, 1048577, file);
    if (n > 1048576 || ferror(file)) return 2;
    input[n] = 0;
    return 0;
}
int main(int argc, char **argv) {
    const char *config = "{\"threads\":4}";
    int first = 0, fresh = 0, i = 2; // first: index of the first --requests file (0: stdin mode)
    if (argc >= 3 && strncmp(argv[2], "--", 2) != 0) config = argv[i++];
    for (; i < argc; ++i) {
        if (!strcmp(argv[i], "--fresh-engine")) fresh = 1;
        else if (!strcmp(argv[i], "--requests") && !first) {
            first = i + 1;
            while (i + 1 < argc && strncmp(argv[i + 1], "--", 2) != 0) ++i;
            if (i < first) first = -1;
        } else first = -1;
    }
    if (argc < 2 || first < 0 || (fresh && !first) || (!first && argc > 3)) {
        fprintf(stderr, "Usage: %s model.gguf [config.json] [--requests FILE... [--fresh-engine]] < request.json\n", argv[0]);
        return 2;
    }
    char *input = malloc(1048578);
    if (!input) return 1;
    dtl_engine *engine = NULL;
    char *error = NULL, *result = NULL;
    dtl_status status = DTL_OK;
    int code = 0;
    if (!first) {
        if (read_request(stdin, input)) { free(input); return 2; }
        status = dtl_open(argv[1], config, &engine, &error);
        if (!status) status = dtl_execute(engine, input, NULL, &result, &error);
        if (status) fprintf(stderr, "%s\n", error ? error : "Laya failed");
        else puts(result);
        dtl_free(error); dtl_free(result); dtl_close(engine); free(input);
        return status ? 1 : 0;
    }
    for (i = first; i < argc && strncmp(argv[i], "--", 2) != 0 && !code; ++i) {
        FILE *file = fopen(argv[i], "rb");
        int bad = !file || read_request(file, input);
        if (file) fclose(file);
        if (bad) { fprintf(stderr, "%s: unreadable or larger than 1 MiB\n", argv[i]); code = 2; break; }
        if (!engine) status = dtl_open(argv[1], config, &engine, &error);
        if (!status) status = dtl_execute(engine, input, NULL, &result, &error);
        if (status) { fprintf(stderr, "%s\n", error ? error : "Laya failed"); code = 1; }
        else { puts(result); fflush(stdout); }
        dtl_free(error); dtl_free(result); error = result = NULL;
        if (fresh) { dtl_close(engine); engine = NULL; }
    }
    dtl_close(engine); free(input);
    return code;
}
