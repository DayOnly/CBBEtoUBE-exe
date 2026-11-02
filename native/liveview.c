/* liveview.c -- the window's Live steps renderer (our own code, no third-party source). #live-steps
 *
 * Rasterises triangles already projected to the screen into a depth buffer and interpolates k per-vertex
 * attributes (normal, colour, ...) across each one for the nearest surface at every pixel centre. Lighting is done
 * by the caller on the buffers. Depth grows away from the camera; the caller fills zbuf with +inf first. Used by the
 * window only, never by a conversion.
 */
#include <stdlib.h>

#define EXPORT __declspec(dllexport)
#define MIN3(a, b, c) ((a) < (b) ? ((a) < (c) ? (a) : (c)) : ((b) < (c) ? (b) : (c)))
#define MAX3(a, b, c) ((a) > (b) ? ((a) > (c) ? (a) : (c)) : ((b) > (c) ? (b) : (c)))

EXPORT int lv_raster(const float *X, const float *Y, const float *Z, const float *A, int k, int nv,
                     const int *tris, int nt, int w, int h, float *zbuf, float *abuf)
{
    int drawn = 0;
    for (int t = 0; t < nt; ++t) {
        const int a = tris[3 * t], b = tris[3 * t + 1], c = tris[3 * t + 2];
        if (a < 0 || b < 0 || c < 0 || a >= nv || b >= nv || c >= nv) continue;
        const float x0 = X[a], y0 = Y[a], x1 = X[b], y1 = Y[b], x2 = X[c], y2 = Y[c];
        float fx0 = MIN3(x0, x1, x2) - 0.5f, fx1 = MAX3(x0, x1, x2) - 0.5f;
        float fy0 = MIN3(y0, y1, y2) - 0.5f, fy1 = MAX3(y0, y1, y2) - 0.5f;
        if (fx1 < 0.0f || fy1 < 0.0f || fx0 > (float)(w - 1) || fy0 > (float)(h - 1)) continue;
        const float area = (x1 - x0) * (y2 - y0) - (x2 - x0) * (y1 - y0);
        if (area > -1e-9f && area < 1e-9f) continue;
        const float inv = 1.0f / area;
        int xmin = (int)(fx0 < 0.0f ? 0.0f : fx0 + 0.999f), xmax = (int)(fx1 > w - 1 ? w - 1 : fx1);
        int ymin = (int)(fy0 < 0.0f ? 0.0f : fy0 + 0.999f), ymax = (int)(fy1 > h - 1 ? h - 1 : fy1);
        if (xmin > xmax || ymin > ymax) {           /* thinner than a pixel: keep its first corner */
            const int px = (int)x0, py = (int)y0;
            if (px >= 0 && py >= 0 && px < w && py < h) { xmin = xmax = px; ymin = ymax = py; }
            else continue;
        }
        for (int iy = ymin; iy <= ymax; ++iy) {
            const float py = iy + 0.5f;
            for (int ix = xmin; ix <= xmax; ++ix) {
                const float px = ix + 0.5f;
                float w0 = ((x1 - px) * (y2 - py) - (x2 - px) * (y1 - py)) * inv;
                float w1 = ((x2 - px) * (y0 - py) - (x0 - px) * (y2 - py)) * inv;
                float w2 = 1.0f - w0 - w1;
                if (w0 < -1e-4f || w1 < -1e-4f || w2 < -1e-4f) {
                    if (!(xmin == xmax && ymin == ymax)) continue;
                    w0 = w1 = w2 = 1.0f / 3.0f;     /* the sub-pixel case above */
                }
                const float z = w0 * Z[a] + w1 * Z[b] + w2 * Z[c];
                const size_t p = (size_t)iy * w + ix;
                if (!(z < zbuf[p])) continue;
                zbuf[p] = z;
                float *o = abuf + p * k;
                const float *pa = A + (size_t)a * k, *pb = A + (size_t)b * k, *pc = A + (size_t)c * k;
                for (int j = 0; j < k; ++j) o[j] = w0 * pa[j] + w1 * pb[j] + w2 * pc[j];
                ++drawn;
            }
        }
    }
    return drawn;
}
