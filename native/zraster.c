/* zraster.c -- visibility test for the body zap (our own code, no third-party source).
 *
 * For each of `nd` orthographic view directions, three depth buffers are rasterised from whole triangles:
 *   near = nearest body surface (front-facing triangles: the nearest body surface always faces the viewer),
 *   far  = farthest body surface (ALL body triangles), garm = nearest garment surface.
 * Depth is smaller toward the viewer. Pixel centres are sampled and each triangle's corners are also written, so
 * slivers thinner than a pixel are never lost. A body vertex is NEEDED in a view when it is the front-most body
 * surface there (within `selftol`) and no garment lies before the body's far side on that line of sight --
 * deleting it would show a hole. `needed` is OR-accumulated: one call per pose gives the union over poses.
 * #zap-tri-vis (zr_needed_t): the same raster also keeps, per pixel, WHICH front-facing body triangle is nearest; a
 * triangle is SEEN when it is nearest at some pixel with no garment before the body's far side. The vertex test
 * alone misses a triangle whose middle shows through a slit while all three of its corners are covered.
 *
 * A pixel missed by the rasteriser leaves a buffer at +/-inf, which reads as "not covered": every approximation
 * here can only mark MORE skin as needed, never less.
 */
#include <math.h>
#include <stdlib.h>

#define EXPORT __declspec(dllexport)
#define MIN3(a, b, c) ((a) < (b) ? ((a) < (c) ? (a) : (c)) : ((b) < (c) ? (b) : (c)))
#define MAX3(a, b, c) ((a) > (b) ? ((a) > (c) ? (a) : (c)) : ((b) > (c) ? (b) : (c)))

/* kind 0: garment -> g (min); with `cull`, a triangle whose cull flag is set (a single-sided shape) is drawn only
 * when it faces the viewer, as the game draws it (#zap-cull). kind 1: body -> front-facing to n (min) and f (max),
 * back-facing to f (max). */
static void raster(const float *X, const float *Y, const float *Z, const int *tris, int nt, int nx,
                   float *g, float *n, float *f, int kind, float front_sign, int *id, const unsigned char *cull)
{
    const float lim = (float)(nx - 1);
    for (int t = 0; t < nt; ++t) {
        const int a = tris[3 * t], b = tris[3 * t + 1], c = tris[3 * t + 2];
        const float x0 = X[a], y0 = Y[a], z0 = Z[a], x1 = X[b], y1 = Y[b], z1 = Z[b], x2 = X[c], y2 = Y[c], z2 = Z[c];
        float fx0 = MIN3(x0, x1, x2), fx1 = MAX3(x0, x1, x2), fy0 = MIN3(y0, y1, y2), fy1 = MAX3(y0, y1, y2);
        if (fx1 < 0.0f || fy1 < 0.0f || fx0 > lim + 1.0f || fy0 > lim + 1.0f) continue;
        const float area = (x1 - x0) * (y2 - y0) - (x2 - x0) * (y1 - y0);
        const int front = kind == 1 && area * front_sign >= 0.0f;
        if (kind == 0 && cull && cull[t] && area * front_sign < 0.0f) continue;
        /* corners */
        const float cx[3] = {x0, x1, x2}, cy[3] = {y0, y1, y2}, cz[3] = {z0, z1, z2};
        for (int k = 0; k < 3; ++k) {
            if (cx[k] < 0.0f || cy[k] < 0.0f || cx[k] >= nx || cy[k] >= nx) continue;
            const int p = (int)cy[k] * nx + (int)cx[k]; const float z = cz[k];
            if (kind == 0) { if (z < g[p]) g[p] = z; }
            else { if (z > f[p]) f[p] = z; if (front && z < n[p]) { n[p] = z; if (id) id[p] = t; } }
        }
        if (area > -1e-12f && area < 1e-12f) continue;
        if (fx0 < 0.0f) fx0 = 0.0f; if (fy0 < 0.0f) fy0 = 0.0f; if (fx1 > lim) fx1 = lim; if (fy1 > lim) fy1 = lim;
        const int xmin = (int)fx0, xmax = (int)fx1, ymin = (int)fy0, ymax = (int)fy1;
        const float inv = 1.0f / area;
        /* barycentric weights are affine in the pixel centre: step along x by a constant */
        const float a0x = -(y2 - y1) * inv, a1x = -(y0 - y2) * inv;
        for (int iy = ymin; iy <= ymax; ++iy) {
            const float py = iy + 0.5f, px0 = xmin + 0.5f;
            float w0 = ((x1 - px0) * (y2 - py) - (x2 - px0) * (y1 - py)) * inv;
            float w1 = ((x2 - px0) * (y0 - py) - (x0 - px0) * (y2 - py)) * inv;
            float *row_g = g + (size_t)iy * nx, *row_n = n + (size_t)iy * nx, *row_f = f + (size_t)iy * nx;
            int *row_i = id ? id + (size_t)iy * nx : NULL;
            for (int ix = xmin; ix <= xmax; ++ix, w0 += a0x, w1 += a1x) {
                const float w2 = 1.0f - w0 - w1;
                if (w0 < -1e-5f || w1 < -1e-5f || w2 < -1e-5f) continue;
                const float z = w0 * z0 + w1 * z1 + w2 * z2;
                if (kind == 0) { if (z < row_g[ix]) row_g[ix] = z; }
                else {
                    if (z > row_f[ix]) row_f[ix] = z;
                    if (front && z < row_n[ix]) { row_n[ix] = z; if (row_i) row_i[ix] = t; }
                }
            }
        }
    }
}

static void project(const float *v, int nv, const float *M, const float *o, float *X, float *Y, float *Z)
{
    for (int i = 0; i < nv; ++i) {
        const float x = v[3 * i], y = v[3 * i + 1], z = v[3 * i + 2];
        X[i] = x * M[0] + y * M[3] + z * M[6] + o[0];
        Y[i] = x * M[1] + y * M[4] + z * M[7] + o[1];
        Z[i] = x * M[2] + y * M[5] + z * M[8] + o[2];
    }
}

/* mats: nd x 9 row-major 3x3 whose columns are (u, v, -d) divided by the pixel size; off: nd x 3 added after the
 * product (z offset 0). Verts float32 xyz, tris int32. nx: buffer side. selftol in pixel units. front_sign: +1 when
 * the body's triangles are wound with outward normals. bn (optional): body vertex normals, outward; dirs: nd x 3
 * view directions (toward the viewer); a view counts for a vertex only when bn.dir >= cmin. Returns 0 or -1
 * (memory). */
static int needed_impl(const float *bv, int nbv, const int *bt, int nbt,
                       const float *gv, int ngv, const int *gt, int ngt,
                       const float *mats, const float *off, int nd, int nx, float selftol,
                       unsigned char *needed, int front_sign, const float *bn, const float *dirs, float cmin,
                       unsigned char *tri_seen, const float *tn, const unsigned char *gcull)
{
    const size_t npx = (size_t)nx * nx;
    float *nb = (float *)malloc(npx * sizeof(float)), *fb = (float *)malloc(npx * sizeof(float));
    float *gb = (float *)malloc(npx * sizeof(float));
    float *BX = (float *)malloc(nbv * sizeof(float)), *BY = (float *)malloc(nbv * sizeof(float));
    float *BZ = (float *)malloc(nbv * sizeof(float));
    const int ng1 = ngv > 0 ? ngv : 1;
    float *GX = (float *)malloc(ng1 * sizeof(float)), *GY = (float *)malloc(ng1 * sizeof(float));
    float *GZ = (float *)malloc(ng1 * sizeof(float));
    int *ib = tri_seen ? (int *)malloc(npx * sizeof(int)) : NULL;
    int rc = 0;
    if (!nb || !fb || !gb || !BX || !BY || !BZ || !GX || !GY || !GZ || (tri_seen && !ib)) { rc = -1; goto done; }
    for (int d = 0; d < nd; ++d) {
        const float *M = mats + 9 * d, *o = off + 3 * d;
        for (size_t i = 0; i < npx; ++i) { nb[i] = INFINITY; fb[i] = -INFINITY; gb[i] = INFINITY; }
        if (ib) for (size_t i = 0; i < npx; ++i) ib[i] = -1;
        project(bv, nbv, M, o, BX, BY, BZ);
        raster(BX, BY, BZ, bt, nbt, nx, gb, nb, fb, 1, (float)front_sign, ib, NULL);
        if (ngt > 0) {
            project(gv, ngv, M, o, GX, GY, GZ);
            raster(GX, GY, GZ, gt, ngt, nx, gb, nb, fb, 0, (float)front_sign, NULL, gcull);
        }
        if (ib) {
            const float *dd = dirs + 3 * d;
            for (size_t p = 0; p < npx; ++p) {
                const int t = ib[p];
                if (t < 0 || tri_seen[t] || gb[p] < fb[p]) continue;
                if (tn && tn[3 * t] * dd[0] + tn[3 * t + 1] * dd[1] + tn[3 * t + 2] * dd[2] < cmin) continue;
                tri_seen[t] = 1;
            }
        }
        for (int i = 0; i < nbv; ++i) {
            if (needed[i]) continue;
            if (BX[i] < 0.0f || BY[i] < 0.0f || BX[i] >= nx || BY[i] >= nx) { needed[i] = 1; continue; }
            const size_t p = (size_t)(int)BY[i] * nx + (int)BX[i];
            /* only views within the angle limit of the skin's outward normal count: a line of sight nearly
             * tangent to the skin sees it edge-on through a slot that projects to almost nothing */
            if (bn && bn[3 * i] * dirs[3 * d] + bn[3 * i + 1] * dirs[3 * d + 1] + bn[3 * i + 2] * dirs[3 * d + 2] < cmin) continue;
            if (BZ[i] <= nb[p] + selftol && !(gb[p] < fb[p])) needed[i] = 1;
        }
    }
done:
    free(nb); free(fb); free(gb); free(BX); free(BY); free(BZ); free(GX); free(GY); free(GZ); free(ib);
    return rc;
}

EXPORT int zr_needed(const float *bv, int nbv, const int *bt, int nbt,
                     const float *gv, int ngv, const int *gt, int ngt,
                     const float *mats, const float *off, int nd, int nx, float selftol,
                     unsigned char *needed, int front_sign, const float *bn, const float *dirs, float cmin)
{
    return needed_impl(bv, nbv, bt, nbt, gv, ngv, gt, ngt, mats, off, nd, nx, selftol, needed, front_sign, bn, dirs,
                       cmin, NULL, NULL, NULL);
}

/* zr_needed plus #zap-tri-vis: OR into tri_seen (uint8 per body triangle) the triangles seen in some view. tn
 * (optional): outward unit triangle normals; a view counts for a triangle only when tn.dir >= cmin, like bn. */
EXPORT int zr_needed_t(const float *bv, int nbv, const int *bt, int nbt,
                       const float *gv, int ngv, const int *gt, int ngt,
                       const float *mats, const float *off, int nd, int nx, float selftol,
                       unsigned char *needed, int front_sign, const float *bn, const float *dirs, float cmin,
                       unsigned char *tri_seen, const float *tn)
{
    return needed_impl(bv, nbv, bt, nbt, gv, ngv, gt, ngt, mats, off, nd, nx, selftol, needed, front_sign, bn, dirs,
                       cmin, tri_seen, tn, NULL);
}

/* zr_needed_t plus #zap-cull: gcull (optional, uint8 per garment triangle): 1 = the triangle's shape is single-sided,
 * so it covers only where it faces the viewer (garment winding follows the body's: front_sign). */
EXPORT int zr_needed_c(const float *bv, int nbv, const int *bt, int nbt,
                       const float *gv, int ngv, const int *gt, int ngt,
                       const float *mats, const float *off, int nd, int nx, float selftol,
                       unsigned char *needed, int front_sign, const float *bn, const float *dirs, float cmin,
                       unsigned char *tri_seen, const float *tn, const unsigned char *gcull)
{
    return needed_impl(bv, nbv, bt, nbt, gv, ngv, gt, ngt, mats, off, nd, nx, selftol, needed, front_sign, bn, dirs,
                       cmin, tri_seen, tn, gcull);
}
