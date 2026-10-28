/* npcache -- a numpy data allocator that keeps freed LARGE blocks for reuse.  #numpy-block-cache
 *
 * WHY. On Windows every large numpy array is a fresh VirtualAlloc: the kernel zero-fills it page by page on first
 * touch and takes it back on free. A full conversion took 2.87 G page faults that way (~10% of its CPU, all kernel
 * time). Handing a freed block straight to the next array of its size class skips both.
 *
 * WHAT IT DOES NOT CHANGE. Arrays under THRESH go to numpy's own default handler untouched. A large block is a plain
 * malloc of its class capacity with NO header -- an array starts at the same kind of address the default allocator
 * gives it -- and its capacity lives in a side table keyed by address. A reused block is never zeroed except for
 * calloc, exactly as fresh malloc memory is not; a test poisons reused blocks to prove no pass reads memory it did
 * not write.
 *
 * MEMORY. Cached bytes stay committed, so the cache is held under BUDGET and is EMPTIED, and stops caching, while the
 * machine's free RAM or free commit (whichever is lower) is under LOW; it resumes above LOW + 1 GB. The machine is
 * read live (GlobalMemoryStatusEx), at most every 100 ms -- nothing from an earlier run.
 */
#define PY_SSIZE_T_CLEAN
#include <Python.h>
#include <windows.h>
#include <string.h>
#define NPY_NO_DEPRECATED_API NPY_2_0_API_VERSION
#define NPY_TARGET_VERSION NPY_2_0_API_VERSION
#include <numpy/arrayobject.h>

static int POISON = 0, suspended = 0;
static size_t THRESH = 256 * 1024, BUDGET = 256ull << 20, cached = 0, peak_cached = 0;
static unsigned long long LOW = 3ull << 30, RESUME = 4ull << 30, last_check = 0;
static unsigned long long hits = 0, misses = 0, overflow = 0, flushes = 0, low_checks = 0;
static SRWLOCK lk = SRWLOCK_INIT;
static PyDataMemAllocator *dflt;                       /* numpy's default handler */
static PyObject *dflt_cap;

/* ---- side table: address -> capacity (open addressing with tombstones) ---- */
#define TB (1u << 20)
static void **tk; static size_t *tv;                    /* key NULL = empty, (void *)1 = tombstone */
static size_t h_of(void *p) { size_t x = (size_t)p >> 4; x ^= x >> 17; x *= 0x9E3779B97F4A7C15ull; return x & (TB - 1); }
static void t_put(void *p, size_t cap) {
    size_t i = h_of(p);
    while (tk[i] && tk[i] != (void *)1) i = (i + 1) & (TB - 1);
    tk[i] = p; tv[i] = cap;
}
static size_t t_take(void *p) {                        /* the capacity, removed; 0 when not ours */
    size_t i = h_of(p);
    while (tk[i]) { if (tk[i] == p) { tk[i] = (void *)1; return tv[i]; } i = (i + 1) & (TB - 1); }
    return 0;
}
static size_t t_get(void *p) {
    size_t i = h_of(p);
    while (tk[i]) { if (tk[i] == p) return tv[i]; i = (i + 1) & (TB - 1); }
    return 0;
}

/* ---- size classes (1/8 of a power of two, <= 12.5% slack) and their free stacks ---- */
#define NC 4096
static size_t ckey[NC]; static void **stk[NC]; static int n[NC], capn[NC];
static size_t round_cap(size_t s) {
    int e = 63 - (int)__lzcnt64((unsigned long long)s);
    size_t base = (size_t)1 << e, step = base >> 3, k = (s - base + step - 1) / step;
    return base + k * step;                            /* k == 8 lands on the next power: one class for it */
}
static int slot(size_t cap, int make) {
    size_t i = (cap * 0x9E3779B97F4A7C15ull >> 40) & (NC - 1);
    for (int g = 0; g < NC; g++, i = (i + 1) & (NC - 1)) {
        if (ckey[i] == cap) return (int)i;
        if (!ckey[i]) { if (!make) return -1; ckey[i] = cap; return (int)i; }
    }
    return -1;
}

/* Under the lock. Frees every cached block. */
static void flush_locked(void) {
    for (int c = 0; c < NC; c++)
        while (n[c] > 0) free(stk[c][--n[c]]);
    cached = 0;
}
/* Under the lock. Re-reads the machine at most every 100 ms; empties and suspends the cache while it is low. */
static void check_machine_locked(void) {
    unsigned long long now = GetTickCount64();
    if (now - last_check < 100) return;
    last_check = now; low_checks++;
    MEMORYSTATUSEX ms; ms.dwLength = sizeof(ms);
    if (!GlobalMemoryStatusEx(&ms)) return;
    unsigned long long avail = ms.ullAvailPhys < ms.ullAvailPageFile ? ms.ullAvailPhys : ms.ullAvailPageFile;
    if (!suspended && avail < LOW) { suspended = 1; if (cached) { flush_locked(); flushes++; } }
    else if (suspended && avail >= RESUME) suspended = 0;
}

static void *big(size_t size, int zero) {
    size_t cap = round_cap(size); void *p = NULL;
    AcquireSRWLockExclusive(&lk);
    check_machine_locked();
    int c = slot(cap, 0);
    if (c >= 0 && n[c] > 0) { p = stk[c][--n[c]]; cached -= cap; hits++; t_put(p, cap); } else misses++;
    ReleaseSRWLockExclusive(&lk);
    if (p) {
        if (zero) memset(p, 0, size);
        else if (POISON) memset(p, 0xCD, cap);
        return p;
    }
    p = zero ? calloc(1, cap) : malloc(cap);
    if (!p) return NULL;
    AcquireSRWLockExclusive(&lk); t_put(p, cap); ReleaseSRWLockExclusive(&lk);
    return p;
}
static void *a_malloc(void *ctx, size_t s) { return s >= THRESH ? big(s, 0) : dflt->malloc(dflt->ctx, s); }
static void *a_calloc(void *ctx, size_t ne, size_t es) {
    size_t s = ne * es;
    if (es && s / es != ne) return NULL;
    return s >= THRESH ? big(s, 1) : dflt->calloc(dflt->ctx, ne, es);
}
static void a_free(void *ctx, void *p, size_t size) {
    if (!p) return;
    AcquireSRWLockExclusive(&lk);
    size_t cap = t_take(p);
    if (cap) {
        check_machine_locked();
        int c = suspended || cached + cap > BUDGET ? -1 : slot(cap, 1);
        if (c >= 0 && n[c] == capn[c]) {
            int nc = capn[c] ? capn[c] * 2 : 8;
            void **s2 = realloc(stk[c], nc * sizeof(void *));
            if (s2) { stk[c] = s2; capn[c] = nc; }
        }
        if (c >= 0 && n[c] < capn[c]) {
            stk[c][n[c]++] = p; cached += cap;
            if (cached > peak_cached) peak_cached = cached;
            p = NULL;
        } else overflow++;
    }
    ReleaseSRWLockExclusive(&lk);
    if (!p) return;
    if (cap) free(p); else dflt->free(dflt->ctx, p, size);
}
static void *a_realloc(void *ctx, void *p, size_t ns) {
    if (!p) return a_malloc(ctx, ns);
    AcquireSRWLockExclusive(&lk); size_t cap = t_get(p); ReleaseSRWLockExclusive(&lk);
    if (!cap) {                                         /* a default-handler block */
        if (ns < THRESH) return dflt->realloc(dflt->ctx, p, ns);
        void *q = big(ns, 0);
        if (!q) return NULL;
        size_t old = _msize(p);                         /* the default handler is plain CRT malloc */
        memcpy(q, p, old < ns ? old : ns);
        dflt->free(dflt->ctx, p, old);
        return q;
    }
    if (ns <= cap && ns >= THRESH) return p;
    void *q = a_malloc(ctx, ns ? ns : 1);
    if (!q) return NULL;
    memcpy(q, p, cap < ns ? cap : ns);
    a_free(ctx, p, cap);
    return q;
}
static PyDataMem_Handler handler = {"npcache", 1, {NULL, a_malloc, a_calloc, a_realloc, a_free}};

/* install(budget_mb, low_mb, thresh_kb=256, poison=0): make this the handler for the calling thread's context. */
static PyObject *install(PyObject *self, PyObject *args) {
    unsigned long long budget_mb, low_mb, thresh_kb = 256;
    if (!PyArg_ParseTuple(args, "KK|Ki", &budget_mb, &low_mb, &thresh_kb, &POISON)) return NULL;
    BUDGET = (size_t)budget_mb << 20; THRESH = (size_t)thresh_kb << 10;
    LOW = low_mb << 20; RESUME = LOW + (1ull << 30);
    if (!tk) {
        tk = calloc(TB, sizeof(void *)); tv = calloc(TB, sizeof(size_t));
        if (!tk || !tv) return PyErr_NoMemory();
    }
    if (!dflt) {
        dflt_cap = PyDataMem_GetHandler();
        if (!dflt_cap) return NULL;
        PyDataMem_Handler *h = (PyDataMem_Handler *)PyCapsule_GetPointer(dflt_cap, "mem_handler");
        if (!h) return NULL;
        dflt = &h->allocator;
    }
    PyObject *cap = PyCapsule_New(&handler, "mem_handler", NULL);
    if (!cap) return NULL;
    PyObject *old = PyDataMem_SetHandler(cap);
    Py_DECREF(cap);
    if (!old) return NULL;
    Py_DECREF(old);
    Py_RETURN_NONE;
}
static PyObject *flush(PyObject *self, PyObject *noargs) {
    AcquireSRWLockExclusive(&lk); flush_locked(); ReleaseSRWLockExclusive(&lk);
    Py_RETURN_NONE;
}
static PyObject *stats(PyObject *self, PyObject *noargs) {
    return Py_BuildValue("{s:K,s:K,s:K,s:K,s:K,s:n,s:n,s:i}", "hits", hits, "misses", misses, "overflow", overflow,
                         "flushes", flushes, "low_checks", low_checks, "cached", (Py_ssize_t)cached,
                         "peak_cached", (Py_ssize_t)peak_cached, "suspended", suspended);
}
static PyMethodDef M[] = {
    {"install", install, METH_VARARGS, NULL}, {"flush", flush, METH_NOARGS, NULL},
    {"stats", stats, METH_NOARGS, NULL}, {NULL}};
static struct PyModuleDef D = {PyModuleDef_HEAD_INIT, "npcache", NULL, -1, M};
PyMODINIT_FUNC PyInit_npcache(void) { import_array(); return PyModule_Create(&D); }
