"""Tests für Mesh-Import, Box-Ausschnitt, Vasen-Heilung (vase_heal) und Mesh-Ersetzung."""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import manifold3d as m3d
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from slicetheprompt import import3mf, mesh_edit, meshcheck as mc, ui_server
from slicetheprompt.stlio import read_stl, write_stl
from slicetheprompt.threemf import write_multipart_3mf


class MeshReplaceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def _make_vase_with_bump(self) -> Path:
        """Erzeugt einen Zylinder (Vase R=20, H=60 bei X=100, Y=100) mit einem seitlichen Auswuchs (Fee bei -Y)."""
        vase = m3d.Manifold.cylinder(60.0, 20.0, 22.0, circular_segments=48).translate([100.0, 100.0, 0.0])
        fairy = m3d.Manifold.cube([12.0, 18.0, 24.0], center=True).translate([100.0, 76.0, 30.0])
        combined = vase + fairy
        tris = mesh_edit.manifold_to_tris(combined)
        p = self.root / "vase_fairy.3mf"
        return write_multipart_3mf(p, [("Vase", tris, 1)], object_name="Vase")

    def _make_dragon_mesh(self) -> Path:
        """Erzeugt ein zweites wasserdichtes Test-Mesh (Drache mit 3 mm Sockel)."""
        base = m3d.Manifold.cylinder(3.5, 10.0, 10.0, circular_segments=32)
        body = m3d.Manifold.sphere(8.0, circular_segments=32).scale([1.0, 1.2, 2.0]).translate([0.0, 0.0, 15.0])
        dragon = base + body
        tris = mesh_edit.manifold_to_tris(dragon)
        p = self.root / "dragon.3mf"
        return write_multipart_3mf(p, [("Dragon", tris, 1)], object_name="Dragon")

    def test_import_and_vase_heal_and_replace(self):
        vase_3mf = self._make_vase_with_bump()
        dragon_3mf = self._make_dragon_mesh()

        with patch.object(mesh_edit, "PROJECTS_DIR", self.root), \
             patch("slicetheprompt.config.PROJECTS_DIR", self.root):
            imp_res = mesh_edit.import_project(vase_3mf, name="test-vase")
            self.assertTrue(imp_res["ok"])
            proj_name = imp_res["project"]
            proj_dir = self.root / proj_name

            # Vor der Heilung ragt die "Fee" bis Y = 67 (100 - 24 - 9 = 67) heraus
            before_y_min = imp_res["bodies"][0]["bbox"][0][1]
            self.assertLess(before_y_min, 70.0)

            # Speichere eine Box-Markierung via UI-Feedback (wie im Studio)
            ui_server.save_project_feedback(proj_dir, {
                "view_mode": "model",
                "annotations": [{
                    "id": 1,
                    "type": "box",
                    "bbox_model": [88.0, 64.0, 15.0, 112.0, 88.0, 45.0],
                    "note": "Fee ersetzen durch Drachen",
                }],
            })
            fb = ui_server.read_project_feedback(proj_name, projects_root=self.root)
            self.assertTrue(fb["ok"])
            self.assertEqual(fb["project_kind"], "import")

            # 1. Nur Fee wegschneiden mit vase_heal (ohne Ersatz) -> Vasenwand bleibt geschlossen!
            res_heal = mesh_edit.replace_in_mesh(
                proj_name,
                replacement_path="",
                mode="vase_heal",
                clear_feedback=False,
            )
            self.assertTrue(res_heal["ok"])
            self.assertTrue(res_heal["passed"])
            healed_tris = read_stl(proj_dir / "out" / "model.stl")
            self.assertGreater(healed_tris.reshape(-1, 3)[:, 1].min(), 77.0)

            # 2. Jetzt Drachen einsetzen (nutzt automatisch das .orig.bak Backup + Box aus Feedback)
            res_rep = mesh_edit.replace_in_mesh(
                proj_name,
                replacement_path=str(dragon_3mf),
                mode="vase_heal",
                trim_bottom_mm=3.0,
                clear_feedback=True,
            )
            self.assertTrue(res_rep["ok"])
            self.assertTrue(res_rep["passed"])
            self.assertTrue((proj_dir / "out" / "render.png").is_file())
            self.assertFalse((proj_dir / "out" / "ui_feedback.json").exists())


if __name__ == "__main__":
    unittest.main(verbosity=2)
