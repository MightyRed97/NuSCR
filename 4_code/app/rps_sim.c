/*
 * rps_sim.c - command-line simulator of the notional four-channel reactor protection system.
 *
 * Runs the C code generated from the function-block design (4_code/src) scan by scan.
 *
 *   rps_sim run TARGET STIMULUS.csv [TRACE.csv]   replay a stimulus, write the trace (default: the screen)
 *   rps_sim interactive [TARGET]                  set inputs and run scans by hand (type help)
 *   rps_sim list [TARGET]                         list the inputs, with their nominal values, and the signals
 *
 * TARGET is BP (one bistable processor), CP (one coincidence processor) or SYSTEM (BP_A..BP_D and CP_A, the
 * default).
 *
 * Stimulus file, the scenario format of 5_simulation/scenarios and tools/nuscr_sim.py: a header 'cycle' followed
 * by input names, then rows 'cycle,value,...'.  A value applies from its cycle on, an empty cell keeps the input's
 * value, lines starting with '#' are comments, and the run lasts until the last cycle listed.  Every input starts
 * at its nominal value.  In SYSTEM a column is INSTANCE.var (BP_A.f_PZR_P), BP.var (all four BPs) or a bare BP
 * input name (all four BPs).
 *
 * Trace: one row per scan: cycle, time_ms, then every computed signal (INSTANCE.signal in SYSTEM).  Flags are 1/0
 * (trip, pre-trip and coincidence signals: 1 = NORMAL, 0 = TRIP); real values have 17 significant digits, so they
 * read back as the same double.
 *
 * Exit status: 0 success, 1 wrong command line, 2 invalid stimulus, 3 file error.  Interactive mode reports an
 * invalid command on the screen and carries on; it exits with 0.
 *
 * STATUS: SYNTHETIC REFERENCE DESIGN - NOT FOR OPERATIONAL, LICENSING OR SAFETY-CLASSIFIED USE.  Every constant is
 * an ILLUSTRATIVE placeholder.
 */
#ifndef _CRT_SECURE_NO_WARNINGS
#define _CRT_SECURE_NO_WARNINGS     /* MSVC: fopen and strtod are used as the C standard defines them */
#endif
#include <ctype.h>
#include <errno.h>
#include <math.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "rps_constants.h"
#include "rps_meta.h"
#include "rps_system.h"

#define MAX_NAME 128
#define MAX_MATCH 8
#define MAX_WATCH 64

enum { EXIT_USAGE = 1, EXIT_INPUT = 2, EXIT_IO = 3 };

/* ------------------------------------------------------------------------------------------------------------ */
/* A simulated target: one BP, one CP, or the system                                                             */
/* ------------------------------------------------------------------------------------------------------------ */

typedef struct Slot {
    char name[MAX_NAME];            /* f_PZR_P, or BP_A.f_PZR_P in SYSTEM */
    char *base;                     /* the structure that holds the value */
    const RPS_Field *field;
    const struct Slot *source;      /* SYSTEM: the signal a connection delivers to this input in every scan */
} Slot;

typedef struct {
    const char *target;
    const RPS_Module *module;       /* BP or CP; NULL for SYSTEM */
    void *state, *inputs, *signals;
    size_t signals_size;
    long cycle;                     /* last scan executed; -1 after power-up */
    Slot *ins, *outs;
    size_t n_ins, n_outs;
} Sim;

static void add_slots(Slot *slots, size_t *n, const char *prefix, char *base, const RPS_Field *fields, size_t count)
{
    size_t i;
    for (i = 0; i < count; i++) {
        Slot *sl = &slots[(*n)++];
        if (prefix != NULL) {
            snprintf(sl->name, sizeof sl->name, "%s.%s", prefix, fields[i].name);
        } else {
            snprintf(sl->name, sizeof sl->name, "%s", fields[i].name);
        }
        sl->base = base;
        sl->field = &fields[i];
        sl->source = NULL;
    }
}

static const Slot *find_slot(const Slot *slots, size_t n, const char *name);

static void sim_reset(Sim *s)
{
    if (s->module == NULL) {
        RPS_System_init((RPS_System_State *)s->state);
        RPS_System_nominal_inputs((RPS_System_Inputs *)s->inputs);
    } else {
        s->module->init(s->state);
        s->module->nominal_inputs(s->inputs);
    }
    memset(s->signals, 0, s->signals_size);
    s->cycle = -1;
}

static void sim_free(Sim *s)
{
    if (s != NULL) {
        free(s->state);
        free(s->inputs);
        free(s->signals);
        free(s->ins);
        free(s->outs);
        free(s);
    }
}

static Sim *sim_new(const char *target)
{
    Sim *s = calloc(1, sizeof *s);
    size_t i, j, n_in = 0, n_out = 0;
    if (s == NULL) {
        return NULL;
    }
    s->target = target;
    if (strcmp(target, "SYSTEM") == 0) {
        s->state = calloc(1, sizeof(RPS_System_State));
        s->inputs = calloc(1, sizeof(RPS_System_Inputs));
        s->signals_size = sizeof(RPS_System_Signals);
        for (i = 0; i < RPS_SYSTEM_N_INSTANCES; i++) {
            n_in += RPS_SYSTEM_INSTANCES[i].module->n_inputs;
            n_out += RPS_SYSTEM_INSTANCES[i].module->n_signals;
        }
    } else {
        s->module = strcmp(target, "BP") == 0 ? &RPS_MODULE_BP : strcmp(target, "CP") == 0 ? &RPS_MODULE_CP : NULL;
        if (s->module == NULL) {
            free(s);
            return NULL;
        }
        s->state = calloc(1, s->module->state_size);
        s->inputs = calloc(1, s->module->inputs_size);
        s->signals_size = s->module->signals_size;
        n_in = s->module->n_inputs;
        n_out = s->module->n_signals;
    }
    s->signals = calloc(1, s->signals_size);
    s->ins = calloc(n_in, sizeof *s->ins);
    s->outs = calloc(n_out, sizeof *s->outs);
    if (s->state == NULL || s->inputs == NULL || s->signals == NULL || s->ins == NULL || s->outs == NULL) {
        sim_free(s);
        return NULL;
    }
    if (s->module == NULL) {
        for (i = 0; i < RPS_SYSTEM_N_INSTANCES; i++) {
            const RPS_Instance *inst = &RPS_SYSTEM_INSTANCES[i];
            add_slots(s->ins, &s->n_ins, inst->name, (char *)s->inputs + inst->inputs_offset, inst->module->inputs,
                      inst->module->n_inputs);
            add_slots(s->outs, &s->n_outs, inst->name, (char *)s->signals + inst->signals_offset,
                      inst->module->signals, inst->module->n_signals);
        }
        for (j = 0; j < RPS_SYSTEM_N_CONNECTIONS; j++) {
            const Slot *src = find_slot(s->outs, s->n_outs, RPS_SYSTEM_CONNECTIONS[j].from);
            if (src == NULL) {
                src = find_slot(s->ins, s->n_ins, RPS_SYSTEM_CONNECTIONS[j].from);
            }
            for (i = 0; i < s->n_ins; i++) {
                if (strcmp(s->ins[i].name, RPS_SYSTEM_CONNECTIONS[j].to) == 0) {
                    s->ins[i].source = src;
                }
            }
        }
    } else {
        add_slots(s->ins, &s->n_ins, NULL, (char *)s->inputs, s->module->inputs, s->module->n_inputs);
        add_slots(s->outs, &s->n_outs, NULL, (char *)s->signals, s->module->signals, s->module->n_signals);
    }
    sim_reset(s);
    return s;
}

static void sim_step(Sim *s)
{
    if (s->module == NULL) {
        RPS_System_step((RPS_System_State *)s->state, (const RPS_System_Inputs *)s->inputs,
                        (RPS_System_Signals *)s->signals);
    } else {
        s->module->step(s->state, s->inputs, s->signals);
    }
    s->cycle++;
}

/* ------------------------------------------------------------------------------------------------------------ */
/* Values                                                                                                        */
/* ------------------------------------------------------------------------------------------------------------ */

static void print_value(FILE *f, const Slot *sl)
{
    const char *p = sl->base + sl->field->offset;
    switch (sl->field->type) {
    case RPS_BOOL:
        fputc(*(const bool *)p ? '1' : '0', f);
        break;
    case RPS_INT32:
        fprintf(f, "%ld", (long)*(const int32_t *)p);
        break;
    default:
        fprintf(f, "%.17g", *(const double *)p);
        break;
    }
}

/* Parses a value for the field's type: a decimal number, 0/1 for a flag.  Returns 0 on success. */
static int parse_value(const char *text, const RPS_Field *f, double *value)
{
    char *end;
    double v;
    errno = 0;
    v = strtod(text, &end);
    while (isspace((unsigned char)*end)) {
        end++;
    }
    if (end == text || *end != '\0' || errno == ERANGE || !isfinite(v)) {
        return -1;
    }
    if (f->type == RPS_BOOL && v != 0.0 && v != 1.0) {
        return -1;
    }
    if (f->type == RPS_INT32 && (v != floor(v) || v < -2147483648.0 || v > 2147483647.0)) {
        return -1;
    }
    *value = v;
    return 0;
}

static void set_value(const Slot *sl, double v)
{
    char *p = sl->base + sl->field->offset;
    switch (sl->field->type) {
    case RPS_BOOL:
        *(bool *)p = v != 0.0;
        break;
    case RPS_INT32:
        *(int32_t *)p = (int32_t)v;
        break;
    default:
        *(double *)p = v;
        break;
    }
}

static const Slot *find_slot(const Slot *slots, size_t n, const char *name)
{
    size_t i;
    for (i = 0; i < n; i++) {
        if (strcmp(slots[i].name, name) == 0) {
            return &slots[i];
        }
    }
    return NULL;
}

/* The input slots an input name stands for (INSTANCE.var, BP.var or a bare BP input name in SYSTEM); returns how
 * many (0: no such input). */
static size_t resolve_input(const Sim *s, const char *name, const Slot **found)
{
    char full[MAX_NAME];
    const char *var = name;
    size_t i, n = 0;
    const Slot *sl;
    if (s->module != NULL) {
        sl = find_slot(s->ins, s->n_ins, name);
        if (sl != NULL) {
            found[n++] = sl;
        }
        return n;
    }
    if (strchr(name, '.') != NULL) {
        if (strncmp(name, "BP.", 3) != 0) {
            sl = find_slot(s->ins, s->n_ins, name);
            if (sl != NULL) {
                found[n++] = sl;
            }
            return n;
        }
        var = name + 3;
    }
    for (i = 0; i < RPS_SYSTEM_N_INSTANCES && n < MAX_MATCH; i++) {
        if (RPS_SYSTEM_INSTANCES[i].module == &RPS_MODULE_BP) {
            snprintf(full, sizeof full, "%s.%s", RPS_SYSTEM_INSTANCES[i].name, var);
            sl = find_slot(s->ins, s->n_ins, full);
            if (sl != NULL) {
                found[n++] = sl;
            }
        }
    }
    return n;
}

static char *trim(char *t)
{
    char *e;
    while (isspace((unsigned char)*t)) {
        t++;
    }
    e = t + strlen(t);
    while (e > t && isspace((unsigned char)e[-1])) {
        *--e = '\0';
    }
    return t;
}

/* ------------------------------------------------------------------------------------------------------------ */
/* Stimulus replay                                                                                               */
/* ------------------------------------------------------------------------------------------------------------ */

typedef struct {
    const Slot *slots[MAX_MATCH];
    size_t n;
    char name[MAX_NAME];
} Column;

typedef struct {
    long cycle;
    size_t order;
    size_t line_no;
    char *cells;                    /* the row after its cycle cell, or NULL */
} Row;

static int compare_rows(const void *a, const void *b)
{
    const Row *x = a, *y = b;
    if (x->cycle != y->cycle) {
        return x->cycle < y->cycle ? -1 : 1;
    }
    return x->order < y->order ? -1 : x->order > y->order ? 1 : 0;
}

static char *read_file(const char *path)
{
    FILE *f = fopen(path, "rb");
    char *buf = NULL;
    size_t size = 0, cap = 0, got;
    if (f == NULL) {
        return NULL;
    }
    for (;;) {
        if (cap - size < 65536) {
            char *bigger;
            cap = cap ? cap * 2 : 1 << 20;
            bigger = realloc(buf, cap + 1);
            if (bigger == NULL) {
                free(buf);
                fclose(f);
                return NULL;
            }
            buf = bigger;
        }
        got = fread(buf + size, 1, cap - size, f);
        size += got;
        if (got == 0) {
            break;
        }
    }
    fclose(f);
    buf[size] = '\0';
    return buf;
}

static bool is_comment_or_blank(const char *line)
{
    while (isspace((unsigned char)*line)) {
        line++;
    }
    return *line == '\0' || *line == '#';
}

static void write_trace_header(FILE *out, const Sim *s)
{
    size_t i;
    fputs("cycle,time_ms", out);
    for (i = 0; i < s->n_outs; i++) {
        fprintf(out, ",%s", s->outs[i].name);
    }
    fputc('\n', out);
}

static void write_trace_row(FILE *out, const Sim *s)
{
    size_t i;
    fprintf(out, "%ld,%.17g", s->cycle, (double)s->cycle * k_Scan_Period);
    for (i = 0; i < s->n_outs; i++) {
        fputc(',', out);
        print_value(out, &s->outs[i]);
    }
    fputc('\n', out);
}

static int replay(Sim *s, const char *path, FILE *out)
{
    char *text = read_file(path), *p, *line, *header = NULL;
    Column *cols = NULL;
    Row *rows = NULL;
    size_t n_cols = 0, n_rows = 0, cap_rows = 0, line_no = 0, header_line = 0, r, i;
    long last = -1, c;
    int status = 0;
    if (text == NULL) {
        fprintf(stderr, "rps_sim: cannot read %s\n", path);
        return EXIT_IO;
    }
    /* split into lines and collect the rows */
    for (p = text; p != NULL && *p != '\0'; ) {
        char *nl = strchr(p, '\n');
        line = p;
        p = nl != NULL ? nl + 1 : NULL;
        if (nl != NULL) {
            *nl = '\0';
        }
        line_no++;
        if (strlen(line) > 0 && line[strlen(line) - 1] == '\r') {
            line[strlen(line) - 1] = '\0';
        }
        if (is_comment_or_blank(line)) {
            continue;
        }
        if (header == NULL) {
            header = line;
            header_line = line_no;
            continue;
        }
        if (n_rows == cap_rows) {
            Row *bigger;
            cap_rows = cap_rows ? cap_rows * 2 : 1024;
            bigger = realloc(rows, cap_rows * sizeof *rows);
            if (bigger == NULL) {
                fprintf(stderr, "rps_sim: out of memory\n");
                status = EXIT_IO;
                goto done;
            }
            rows = bigger;
        }
        {
            char *comma = strchr(line, ',');
            char *end;
            Row *row = &rows[n_rows];
            if (comma != NULL) {
                *comma = '\0';
            }
            errno = 0;
            row->cycle = strtol(trim(line), &end, 10);
            if (*trim(line) == '\0' || *end != '\0' || errno == ERANGE || row->cycle < 0) {
                fprintf(stderr, "%s:%zu: the cycle must be a whole number >= 0\n", path, line_no);
                status = EXIT_INPUT;
                goto done;
            }
            row->order = n_rows;
            row->line_no = line_no;
            row->cells = comma != NULL ? comma + 1 : NULL;
            if (row->cycle > last) {
                last = row->cycle;
            }
            n_rows++;
        }
    }
    if (header == NULL) {
        fprintf(stderr, "%s: no header\n", path);
        status = EXIT_INPUT;
        goto done;
    }
    /* header: cycle, then input names */
    {
        char *cell = header, *next;
        size_t k = 0;
        for (k = 0; cell != NULL; k++) {
            next = strchr(cell, ',');
            if (next != NULL) {
                *next++ = '\0';
            }
            cell = trim(cell);
            if (k == 0) {
                if (strcmp(cell, "cycle") != 0) {
                    fprintf(stderr, "%s:%zu: the first column must be 'cycle'\n", path, header_line);
                    status = EXIT_INPUT;
                    goto done;
                }
            } else {
                Column *bigger = realloc(cols, (n_cols + 1) * sizeof *cols);
                if (bigger == NULL) {
                    fprintf(stderr, "rps_sim: out of memory\n");
                    status = EXIT_IO;
                    goto done;
                }
                cols = bigger;
                snprintf(cols[n_cols].name, sizeof cols[n_cols].name, "%s", cell);
                cols[n_cols].n = resolve_input(s, cell, cols[n_cols].slots);
                if (cols[n_cols].n == 0) {
                    fprintf(stderr, "%s:%zu: %s is not an input of %s\n", path, header_line, cell, s->target);
                    status = EXIT_INPUT;
                    goto done;
                }
                n_cols++;
            }
            cell = next;
        }
    }
    qsort(rows, n_rows, sizeof *rows, compare_rows);
    write_trace_header(out, s);
    r = 0;
    for (c = 0; c <= last; c++) {
        for (; r < n_rows && rows[r].cycle == c; r++) {
            char *cell = rows[r].cells, *next;
            size_t k;
            for (k = 0; cell != NULL; k++) {
                double v;
                next = strchr(cell, ',');
                if (next != NULL) {
                    *next++ = '\0';
                }
                cell = trim(cell);
                if (k >= n_cols) {
                    fprintf(stderr, "%s:%zu: more cells than columns\n", path, rows[r].line_no);
                    status = EXIT_INPUT;
                    goto done;
                }
                if (*cell != '\0') {
                    if (parse_value(cell, cols[k].slots[0]->field, &v) != 0) {
                        fprintf(stderr, "%s:%zu: %s = %s is not a valid value\n", path, rows[r].line_no,
                                cols[k].name, cell);
                        status = EXIT_INPUT;
                        goto done;
                    }
                    for (i = 0; i < cols[k].n; i++) {
                        set_value(cols[k].slots[i], v);
                    }
                }
                cell = next;
            }
        }
        sim_step(s);
        write_trace_row(out, s);
    }
done:
    free(cols);
    free(rows);
    free(text);
    return status;
}

/* ------------------------------------------------------------------------------------------------------------ */
/* Listing and interactive use                                                                                   */
/* ------------------------------------------------------------------------------------------------------------ */

static void describe(FILE *f, const Slot *sl, long cycle)
{
    const Slot *shown = sl->source != NULL && cycle >= 0 ? sl->source : sl;   /* a driven input: what it receives */
    const char *p = shown->base + shown->field->offset;
    fprintf(f, "  %-34s = ", sl->name);
    print_value(f, shown);
    if (sl->field->type == RPS_BOOL && sl->field->label0 != NULL) {
        fprintf(f, " (%s)", *(const bool *)p ? sl->field->label1 : sl->field->label0);
    }
    if (sl->field->unit != NULL && sl->field->unit[0] != '\0') {
        fprintf(f, " %s", sl->field->unit);
    }
    if (sl->source != NULL) {
        fprintf(f, "  [from %s]", sl->source->name);
    }
    fputc('\n', f);
}

static bool contains_nocase(const char *text, const char *part)
{
    size_t n = strlen(part), i, j;
    for (i = 0; text[i] != '\0'; i++) {
        for (j = 0; j < n && text[i + j] != '\0'; j++) {
            if (tolower((unsigned char)text[i + j]) != tolower((unsigned char)part[j])) {
                break;
            }
        }
        if (j == n) {
            return true;
        }
    }
    return n == 0;
}

static void list(const Sim *s, const char *filter)
{
    size_t i;
    printf("Inputs of %s (current values; after power-up these are the nominal values):\n", s->target);
    for (i = 0; i < s->n_ins; i++) {
        if (filter == NULL || contains_nocase(s->ins[i].name, filter)) {
            describe(stdout, &s->ins[i], s->cycle);
        }
    }
    printf("Signals of %s, %s:\n", s->target, s->cycle < 0 ? "not computed yet (no scan run)" : "after the last scan");
    for (i = 0; i < s->n_outs; i++) {
        if (filter == NULL || contains_nocase(s->outs[i].name, filter)) {
            describe(stdout, &s->outs[i], s->cycle);
        }
    }
}

static const char *HELP =
    "Commands:\n"
    "  set NAME VALUE      set an input; it applies from the next scan and keeps its value\n"
    "                      (SYSTEM: BP_A.f_PZR_P, BP.f_PZR_P or f_PZR_P for all four BPs)\n"
    "  step [N]            run N scans (default 1) and print the watched signals\n"
    "  watch NAME,...      signals or inputs to print after every scan; 'watch' alone clears the list\n"
    "  show [TEXT]         print every input and signal whose name contains TEXT\n"
    "  reset               power-up: initial states and nominal inputs\n"
    "  help, quit\n"
    "Flags are 1/0; trip, pre-trip and coincidence signals are 1 = NORMAL, 0 = TRIP.\n";

static void print_watch(const Sim *s, const Slot **watch, size_t n_watch)
{
    size_t i;
    printf("cycle %ld (t = %.17g ms)\n", s->cycle, (double)s->cycle * k_Scan_Period);
    for (i = 0; i < n_watch; i++) {
        describe(stdout, watch[i], s->cycle);
    }
}

static int interactive(Sim *s)
{
    char line[4096];
    const Slot *watch[MAX_WATCH];
    size_t n_watch = 0;
    printf("rps_sim: %s at power-up, scan period %.17g ms.  Type help for the commands.\n", s->target,
           k_Scan_Period);
    for (;;) {
        char *cmd, *arg;
        printf("rps> ");
        fflush(stdout);
        if (fgets(line, sizeof line, stdin) == NULL) {
            break;
        }
        cmd = trim(line);
        arg = cmd;
        while (*arg != '\0' && !isspace((unsigned char)*arg)) {
            arg++;
        }
        if (*arg != '\0') {
            *arg++ = '\0';
        }
        arg = trim(arg);
        if (*cmd == '\0' || *cmd == '#') {
            continue;
        }
        if (strcmp(cmd, "quit") == 0 || strcmp(cmd, "exit") == 0) {
            break;
        } else if (strcmp(cmd, "help") == 0 || strcmp(cmd, "?") == 0) {
            fputs(HELP, stdout);
        } else if (strcmp(cmd, "reset") == 0) {
            sim_reset(s);
            puts("power-up: initial states, nominal inputs");
        } else if (strcmp(cmd, "show") == 0) {
            list(s, *arg != '\0' ? arg : NULL);
        } else if (strcmp(cmd, "step") == 0) {
            char *end = arg;
            long n = *arg != '\0' ? strtol(arg, &end, 10) : 1, k;
            if (*arg != '\0' && (*end != '\0' || n < 1)) {
                puts("step: N must be a whole number >= 1");
                continue;
            }
            for (k = 0; k < n; k++) {
                sim_step(s);
                if (n <= 20 || k == n - 1) {
                    print_watch(s, watch, n_watch);
                }
            }
        } else if (strcmp(cmd, "watch") == 0) {
            char *name = arg, *next;
            n_watch = 0;
            while (*name != '\0') {
                const Slot *sl;
                next = strchr(name, ',');
                if (next != NULL) {
                    *next++ = '\0';
                }
                name = trim(name);
                sl = find_slot(s->outs, s->n_outs, name);
                if (sl == NULL) {
                    sl = find_slot(s->ins, s->n_ins, name);
                }
                if (sl == NULL) {
                    printf("watch: no signal or input %s\n", name);
                } else if (n_watch < MAX_WATCH) {
                    watch[n_watch++] = sl;
                }
                name = next != NULL ? next : name + strlen(name);
            }
            printf("watching %zu name(s)\n", n_watch);
        } else if (strcmp(cmd, "set") == 0) {
            const Slot *found[MAX_MATCH];
            char *value = arg;
            size_t n, i;
            double v;
            while (*value != '\0' && !isspace((unsigned char)*value)) {
                value++;
            }
            if (*value != '\0') {
                *value++ = '\0';
            }
            value = trim(value);
            n = resolve_input(s, arg, found);
            if (n == 0) {
                printf("set: %s is not an input of %s (try: show %s)\n", arg, s->target, arg);
            } else if (parse_value(value, found[0]->field, &v) != 0) {
                printf("set: %s is not a valid value for %s%s\n", value, arg,
                       found[0]->field->type == RPS_BOOL ? " (use 0 or 1)" : "");
            } else {
                for (i = 0; i < n; i++) {
                    set_value(found[i], v);
                    if (found[i]->source != NULL) {
                        printf("note: %s receives %s in every scan; the value set here is not used\n",
                               found[i]->name, found[i]->source->name);
                    }
                }
                printf("%s = %s from the next scan%s\n", arg, value, n > 1 ? " (every BP)" : "");
            }
        } else {
            printf("unknown command %s; type help\n", cmd);
        }
    }
    return 0;
}

/* ------------------------------------------------------------------------------------------------------------ */

static int usage(void)
{
    fputs("usage: rps_sim run TARGET STIMULUS.csv [TRACE.csv]\n"
          "       rps_sim interactive [TARGET]\n"
          "       rps_sim list [TARGET]\n"
          "TARGET: BP, CP or SYSTEM (default SYSTEM)\n", stderr);
    return EXIT_USAGE;
}

int main(int argc, char **argv)
{
    const char *target;
    Sim *s;
    int status;
    if (argc < 2) {
        return usage();
    }
    if (strcmp(argv[1], "run") == 0 && (argc == 4 || argc == 5)) {
        target = argv[2];
    } else if ((strcmp(argv[1], "interactive") == 0 || strcmp(argv[1], "list") == 0) && argc <= 3) {
        target = argc == 3 ? argv[2] : "SYSTEM";
    } else {
        return usage();
    }
    s = sim_new(target);
    if (s == NULL) {
        fprintf(stderr, "rps_sim: unknown target %s (use BP, CP or SYSTEM)\n", target);
        return EXIT_USAGE;
    }
    if (strcmp(argv[1], "run") == 0) {
        FILE *out = stdout;
        static char buffer[1 << 16];
        if (argc == 5) {
            out = fopen(argv[4], "w");
            if (out == NULL) {
                fprintf(stderr, "rps_sim: cannot write %s\n", argv[4]);
                sim_free(s);
                return EXIT_IO;
            }
        }
        setvbuf(out, buffer, _IOFBF, sizeof buffer);
        status = replay(s, argv[3], out);
        if (fflush(out) != 0 || (out != stdout && fclose(out) != 0)) {
            fprintf(stderr, "rps_sim: error writing the trace\n");
            status = status ? status : EXIT_IO;
        }
    } else if (strcmp(argv[1], "list") == 0) {
        list(s, NULL);
        status = 0;
    } else {
        status = interactive(s);
    }
    sim_free(s);
    return status;
}
