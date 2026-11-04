"""STL lesen und schreiben (binär und ASCII), nur mit numpy.

Ein Netz ist hier immer ein Array der Form (n, 3, 3): n Dreiecke, je 3 Ecken mit x, y, z in mm.
"""

from __future__ import annotations

import struct
from pathlib import Path

import numpy as np


def read_stl(path: str | Path) -> np.ndarray:
    data = Path(path).read_bytes()
    if len(data) < 84:
        raise ValueError(f"{path}: Datei zu klein für STL")
    n = struct.unpack("<I", data[80:84])[0]
    if 84 + 50 * n == len(data):
        rec = np.dtype([("normal", "<f4", 3), ("v", "<f4", (3, 3)), ("attr", "<u2")])
        arr = np.frombuffer(data, dtype=rec, count=n, offset=84)
        return arr["v"].astype(np.float64)
    # ASCII
    text = data.decode("ascii", errors="ignore")
    verts = []
    for line in text.splitlines():
        line = line.strip()
        if line.startswith("vertex"):
            parts = line.split()
            verts.append([float(parts[1]), float(parts[2]), float(parts[3])])
    if not verts or len(verts) % 3:
        raise ValueError(f"{path}: kein gültiges STL")
    return np.asarray(verts, dtype=np.float64).reshape(-1, 3, 3)


def write_stl(path: str | Path, tris: np.ndarray, header: str = "printlab") -> None:
    tris = np.asarray(tris, dtype=np.float64)
    normals = face_normals(tris)
    rec = np.dtype([("normal", "<f4", 3), ("v", "<f4", (3, 3)), ("attr", "<u2")])
    arr = np.zeros(len(tris), dtype=rec)
    arr["normal"] = normals
    arr["v"] = tris
    head = header.encode("ascii", errors="ignore")[:80].ljust(80, b" ")
    with open(path, "wb") as f:
        f.write(head)
        f.write(struct.pack("<I", len(tris)))
        f.write(arr.tobytes())


def face_normals(tris: np.ndarray) -> np.ndarray:
    n = np.cross(tris[:, 1] - tris[:, 0], tris[:, 2] - tris[:, 0])
    length = np.linalg.norm(n, axis=1, keepdims=True)
    length[length == 0] = 1.0
    return n / length


def face_areas(tris: np.ndarray) -> np.ndarray:
    n = np.cross(tris[:, 1] - tris[:, 0], tris[:, 2] - tris[:, 0])
    return 0.5 * np.linalg.norm(n, axis=1)
