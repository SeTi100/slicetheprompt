"""3MF previews and drop uploads, without CAD construction, slicer or printer."""
from __future__ import annotations

import io
import json
import os
from pathlib import Path
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch
import urllib.error
import urllib.parse
import urllib.request
import zipfile

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from slicetheprompt import import3mf as imp, pipeline, threemf, ui_server
from slicetheprompt.stlio import read_stl

NS = imp.CORE
REL = '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="r" Type="http://schemas.microsoft.com/3dmanufacturing/2013/01/3dmodel" Target="/3D/3dmodel.model"/></Relationships>'
MESH = '<object id="1" name="Test" pid="10" pindex="0"><mesh><vertices><vertex x="0" y="0" z="0"/><vertex x="2" y="0" z="0"/><vertex x="0" y="3" z="0"/><vertex x="0" y="0" z="4"/></vertices><triangles><triangle v1="0" v2="2" v3="1"/><triangle v1="0" v2="1" v3="3"/><triangle v1="1" v2="2" v3="3"/><triangle v1="0" v2="3" v3="2"/></triangles></mesh></object>'
BASE = '<basematerials id="10"><base name="rot" displaycolor="#FF0000FF"/></basematerials>'
IDENT = '1 0 0 0 1 0 0 0 1 0 0 0'


def archive(path: Path, resources=MESH + BASE, items='<item objectid="1"/>', unit="millimeter",
            extra=None, attrs="") -> Path:
    model = f'<model xmlns="{NS}" unit="{unit}" {attrs}><resources>{resources}</resources><build>{items}</build></model>'
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("_rels/.rels", REL)
        z.writestr("3D/3dmodel.model", model)
        for key, value in (extra or {}).items():
            z.writestr(key, value)
    return path


class ImportTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.project = self.root / "loose"
        self.project.mkdir()

    def tearDown(self):
        self.tmp.cleanup()

    def test_units_colors_and_nested_transform_order(self):
        # A 90-degree rotation of a translated child is different from translating a rotated child.
        resources = MESH + BASE + '<object id="2"><components><component objectid="1" transform="1 0 0 0 1 0 0 0 1 5 0 0"/></components></object>'
        p = archive(self.project / "a.3mf", resources, '<item objectid="2" transform="0 1 0 -1 0 0 0 0 1 10 20 0"/>', "inch")
        body = imp.read_3mf(p)["bodies"][0]
        pts = body["tris"].reshape(-1, 3)
        np.testing.assert_allclose(pts.min(0), np.array([7, 25, 0]) * 25.4)
        np.testing.assert_allclose(pts.max(0), np.array([10, 27, 4]) * 25.4)
        self.assertEqual(body["color"], "#FF0000")

    def test_production_references_instances_names_and_filaments(self):
        tris = np.array([[[0, 0, 0], [1, 0, 0], [0, 1, 0]]], dtype=float)
        p = threemf.write_multipart_3mf(self.project / "multi.3mf", [("Grund", tris, 1), ("Einlage", tris + [0, 0, 1], 2)])
        with zipfile.ZipFile(p, "a") as z:
            z.writestr("Metadata/project_settings.config", json.dumps({"filament_colour": ["#FFFFFF", "#00FF00"]}))
        data = imp.read_3mf(p)
        self.assertEqual([b["name"] for b in data["bodies"]], ["Grund", "Einlage"])
        self.assertEqual([b["color"] for b in data["bodies"]], ["#FFFFFF", "#00FF00"])
        self.assertEqual([b["plate"] for b in data["bodies"]], ["1", "1"])

    def test_multiple_plates_and_repeated_body_names(self):
        settings = '<config><plate><metadata key="plater_name" value="Erste"/><model_instance><metadata key="object_id" value="1"/><metadata key="instance_id" value="0"/></model_instance></plate><plate><metadata key="plater_name" value="Zweite"/><model_instance><metadata key="object_id" value="1"/><metadata key="instance_id" value="1"/></model_instance></plate></config>'
        archive(self.project / "a.3mf", items='<item objectid="1"/><item objectid="1" transform="1 0 0 0 1 0 0 0 1 100 0 0"/>',
                extra={"Metadata/model_settings.config": settings})
        state = ui_server.build_project_state(self.project, plate="2")
        self.assertEqual(state["import"]["plate"], "2")
        self.assertEqual(len(state["meshes"]), 1)
        self.assertEqual(state["meshes"][0]["name"], "Test")
        data = imp.preview(self.project)
        self.assertEqual(len({b["id"] for b in data["bodies"]}), 2)
        self.assertEqual([b["plate"] for b in data["bodies"]], ["1", "2"])

    def test_cache_replacement_isolation_and_source_unchanged(self):
        p = archive(self.project / "a.3mf")
        original = p.read_bytes()
        one = imp.preview(self.project)
        self.assertEqual(p.read_bytes(), original)
        with patch.object(imp, "read_3mf", side_effect=AssertionError("cache should be reused")):
            self.assertEqual(imp.preview(self.project)["sha256"], one["sha256"])
        archive(p, unit="centimeter")
        two = imp.preview(self.project)
        self.assertNotEqual(one["sha256"], two["sha256"])
        np.testing.assert_allclose(read_stl(self.project / two["bodies"][0]["stl"]).max((0, 1)), [20, 30, 40])
        p.write_bytes(b"broken")
        state = ui_server.build_project_state(self.project)
        self.assertTrue(state["import"]["error"])
        self.assertEqual(state["meshes"], [])  # Never show a stale success.

    def test_discovery_three_files_and_cad_precedence(self):
        for name in ("a.3mf", "b.3MF", "dritte Datei.3mf"):
            archive(self.project / name)
        summaries = ui_server.list_projects_summary(self.root)
        self.assertEqual(summaries[0]["kind"], "import")
        self.assertEqual(len(summaries[0]["sources"]), 3)
        with patch.object(pipeline, "PROJECTS_DIR", self.root):
            self.assertEqual(len(pipeline.list_projects()[0]["sources"]), 3)
        state = ui_server.build_project_state(self.project, filename="dritte Datei.3mf")
        self.assertEqual(state["import"]["source"], "dritte Datei.3mf")
        self.assertEqual(state["report"], {})
        self.assertFalse((self.project / "spec.yaml").exists())
        self.assertFalse((self.project / "model.py").exists())
        (self.project / "assembly.py").write_text("TEILE = {}")
        self.assertEqual(imp.project_kind(self.project), "assembly")
        (self.project / "model.py").write_text("part = None")
        self.assertEqual(imp.project_kind(self.project), "cad")

    def test_watch_add_replace_and_delete_even_with_old_mtime(self):
        a = archive(self.project / "a.3mf")
        first = ui_server._projects_watch_signature(self.root)
        b = archive(self.project / "b.3mf")
        os.utime(b, (1, 1))
        second = ui_server._projects_watch_signature(self.root)
        self.assertNotEqual(first, second)
        a.unlink()
        third = ui_server._projects_watch_signature(self.root)
        self.assertNotEqual(second, third)
        b.unlink()
        self.assertNotIn("loose", ui_server._projects_watch_signature(self.root))

    def test_invalid_geometry_cycles_extensions_and_external_paths(self):
        cases = [
            {"resources": MESH.replace('v1="0"', 'v1="99"')},
            {"resources": '<object id="1"><components><component objectid="1"/></components></object>'},
            {"attrs": 'xmlns:x="urn:unsupported" requiredextensions="x"'},
            {"resources": '<object id="1"><components><component objectid="2" q:path="../../bad.model"/></components></object>',
             "attrs": f'xmlns:q="{imp.PROD}"'},
            {"items": '<item objectid="1" transform="nan 0 0 0 1 0 0 0 1 0 0 0"/>'},
        ]
        for kwargs in cases:
            with self.subTest(kwargs=kwargs):
                p = archive(self.project / "invalid.3mf", **kwargs)
                with self.assertRaises(ValueError):
                    imp.read_3mf(p)

    def test_archive_limits_and_xml_entities(self):
        p = archive(self.project / "a.3mf")
        with patch.object(imp, "MAX_MEMBER", 10):
            with self.assertRaises(ValueError):
                imp.read_3mf(p)
        with zipfile.ZipFile(p, "w") as z:
            z.writestr("_rels/.rels", '<!DOCTYPE foo [<!ENTITY x "abc">]><Relationships/>')
        with self.assertRaises(ValueError):
            imp.read_3mf(p)

    def test_upload_batches_are_unique_and_atomic(self):
        data = archive(self.project / "source.3mf").read_bytes()
        root = self.root / "uploads"
        first = imp.save_uploads(root, [("a.3mf", data), ("b.3mf", data), ("c.3mf", data)])
        second = imp.save_uploads(root, [("a.3mf", data)])
        self.assertNotEqual(first.name, second.name)
        self.assertEqual(len(imp.source_files(first)), 3)
        before = sorted(root.iterdir())
        for files in [[("../evil.3mf", data)], [("x.3mf", data), ("X.3mf", data)],
                      [("x.3mf", data), ("bad.3mf", b"bad")], [("CON.3mf", data)]]:
            with self.assertRaises(ValueError):
                imp.save_uploads(root, files)
            self.assertEqual(before, sorted(root.iterdir()))

    def test_http_upload_select_preview_and_reject_cad_mutations(self):
        data = archive(self.project / "seed.3mf").read_bytes()
        server = ui_server.create_server("127.0.0.1", 0, projects_dir=self.root)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        base = f"http://127.0.0.1:{server.server_port}"
        boundary = "studio-test"
        payload = b""
        for name in ["a.3mf", "b.3mf", "Gehäuse mit Deckel.3mf"]:
            payload += (f'--{boundary}\r\nContent-Disposition: form-data; name="files"; filename="{name}"\r\nContent-Type: application/octet-stream\r\n\r\n'.encode() + data + b"\r\n")
        payload += f"--{boundary}--\r\n".encode()
        headers = {"Content-Type": f"multipart/form-data; boundary={boundary}"}
        try:
            request = urllib.request.Request(base + "/api/imports", payload, headers)
            with urllib.request.urlopen(request) as response:
                self.assertEqual(response.status, 201)
                project = json.load(response)["project"]
            url = base + f"/api/projects/{project}"
            with urllib.request.urlopen(url + "/state?file=" + urllib.parse.quote("Gehäuse mit Deckel.3mf")) as response:
                state = json.load(response)
                self.assertEqual(state["import"]["source"], "Gehäuse mit Deckel.3mf")
            with urllib.request.urlopen(base + state["meshes"][0]["model_url"]) as response:
                self.assertGreater(len(response.read()), 84)
            for action in ("params", "rebuild", "feedback", "restore"):
                with self.assertRaises(urllib.error.HTTPError) as error:
                    urllib.request.urlopen(urllib.request.Request(url + "/" + action, b"{}", {"Content-Type": "application/json"}))
                self.assertEqual(error.exception.code, 409)
            with self.assertRaises(urllib.error.HTTPError) as error:
                urllib.request.urlopen(urllib.request.Request(base + "/api/imports", payload,
                    {**headers, "Origin": "https://other.example"}))
            self.assertEqual(error.exception.code, 403)
        finally:
            server.shutdown()
            server.server_close()
            thread.join(5)


if __name__ == "__main__":
    unittest.main(verbosity=2)
