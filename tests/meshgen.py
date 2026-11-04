"""Testkörper aus Voxeln: liefert ein geschlossenes Dreiecksnetz mit Normalen nach aussen."""

import numpy as np


def voxels_to_tris(vox: np.ndarray, pitch: float = 1.0) -> np.ndarray:
    vox = np.pad(vox.astype(bool), 1)
    tris = []
    # (Achse, Richtung): Fläche zwischen gefülltem und leerem Voxel
    for axis in range(3):
        for sign in (1, -1):
            shifted = np.roll(vox, -sign, axis=axis)
            faces = vox & ~shifted
            for idx in np.argwhere(faces):
                c = idx.astype(float)
                # Eckpunkte der Würfelfläche
                o = c.copy()
                if sign == 1:
                    o[axis] += 1
                u = np.zeros(3); v = np.zeros(3)
                u[(axis + 1) % 3] = 1
                v[(axis + 2) % 3] = 1
                p0, p1, p2, p3 = o, o + u, o + u + v, o + v
                if sign == 1:
                    tris += [[p0, p1, p2], [p0, p2, p3]]
                else:
                    tris += [[p0, p2, p1], [p0, p3, p2]]
    t = (np.array(tris) - 1) * pitch
    return t


def box(x, y, z):
    v = np.ones((x, y, z), bool)
    return voxels_to_tris(v)
