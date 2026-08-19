import pytest
from fastapi.testclient import TestClient

from ctt.server import app


@pytest.fixture
def client():
    return TestClient(app)


class TestConfigApi:
    def test_returns_fields_with_metadata(self, client):
        body = client.get("/api/config").json()
        paths = {f["path"] for f in body["fields"]}
        assert "target_lang" in paths
        assert "translate.llamacpp.n_gpu_layers" in paths
        assert "typeset.bubble_inset" in paths

    def test_field_types_are_reported(self, client):
        fields = {f["path"]: f for f in client.get("/api/config").json()["fields"]}
        assert fields["detect.threshold"]["type"] == "float"
        assert fields["translate.llamacpp.n_threads"]["type"] == "int"
        assert fields["skip_thumbnails"]["type"] == "bool"
        assert fields["ocr.languages"]["type"] == "list"

    def test_non_obvious_fields_carry_docs(self, client):
        fields = {f["path"]: f for f in client.get("/api/config").json()["fields"]}
        # This one in particular: it decides whether the GPU stays free.
        assert fields["translate.llamacpp.n_gpu_layers"]["doc"]

    def test_choices_offered_where_the_value_is_an_enum(self, client):
        fields = {f["path"]: f for f in client.get("/api/config").json()["fields"]}
        assert fields["detect.model"]["choices"] == ["int8", "fp32", "small"]

    def test_unknown_setting_is_rejected(self, client, tmp_path):
        # A typo must not silently write a dead key into ctt.toml.
        target = tmp_path / "ctt.toml"
        target.write_text('target_lang = "zh-Hans"', encoding="utf-8")
        response = client.put("/api/config", json={
            "fields": {"detect.thresold": 0.5},
            "path": str(target),
        })
        assert response.status_code == 400
        assert "thresold" in response.json()["detail"]

    def test_round_trips_through_toml(self, client, tmp_path):
        target = tmp_path / "ctt.toml"
        target.write_text('target_lang = "zh-Hans"', encoding="utf-8")

        client.put("/api/config", json={
            "fields": {
                "target_lang": "zh-Hant",
                "translate.llamacpp.n_threads": 12,
                "detect.threshold": 0.5,
                "skip_thumbnails": False,
            },
            "glossary": {"LIAM": "利亚姆"},
            "path": str(target),
        })

        from ctt.config import load

        reloaded, warnings = load(target, use_env=False)
        assert warnings == [], "generated TOML must not contain unknown keys"
        assert reloaded.target_lang == "zh-Hant"
        assert reloaded.translate.llamacpp.n_threads == 12
        assert reloaded.detect.threshold == 0.5
        assert reloaded.skip_thumbnails is False
        assert reloaded.glossary == {"LIAM": "利亚姆"}


class TestJobApi:
    def test_rejects_a_request_with_no_input(self, client, tmp_path):
        r = client.post("/api/jobs", json={"output_dir": str(tmp_path)})
        assert r.status_code == 400

    def test_rejects_a_missing_directory(self, client, tmp_path):
        r = client.post("/api/jobs", json={
            "input_dir": str(tmp_path / "nope"),
            "output_dir": str(tmp_path),
        })
        assert r.status_code == 400

    def test_rejects_a_directory_with_no_images(self, client, tmp_path):
        (tmp_path / "notes.txt").write_text("hi")
        r = client.post("/api/jobs", json={
            "input_dir": str(tmp_path), "output_dir": str(tmp_path / "out"),
        })
        assert r.status_code == 400

    def test_unknown_job_is_404(self, client):
        assert client.get("/api/jobs/deadbeef").status_code == 404

    def test_cancelling_an_unknown_job_is_rejected(self, client):
        assert client.post("/api/jobs/deadbeef/cancel").status_code == 400


class TestBrowse:
    def test_lists_subdirectories_with_image_counts(self, client, tmp_path):
        (tmp_path / "chapter").mkdir()
        (tmp_path / "chapter" / "a.jpg").write_bytes(b"x" * 10)
        (tmp_path / "chapter" / "b.png").write_bytes(b"x" * 10)
        (tmp_path / "chapter" / "notes.txt").write_text("no")

        body = client.get("/api/browse", params={"path": str(tmp_path)}).json()
        entry = next(e for e in body["entries"] if e["name"] == "chapter")
        assert entry["images"] == 2

    def test_hidden_directories_are_skipped(self, client, tmp_path):
        (tmp_path / ".git").mkdir()
        (tmp_path / "visible").mkdir()
        names = {e["name"] for e in client.get(
            "/api/browse", params={"path": str(tmp_path)}
        ).json()["entries"]}
        assert names == {"visible"}

    def test_reports_parent_for_navigation(self, client, tmp_path):
        child = tmp_path / "child"
        child.mkdir()
        body = client.get("/api/browse", params={"path": str(child)}).json()
        assert body["parent"] == str(tmp_path)

    def test_non_directory_is_rejected(self, client, tmp_path):
        f = tmp_path / "a.txt"
        f.write_text("x")
        assert client.get("/api/browse", params={"path": str(f)}).status_code == 400


class TestTypesetPreviewApi:
    """The preview is only useful if it is honest: it must run the real layout
    engine and report what the settings did, not just return a picture."""

    def facts(self, client, **body):
        import json

        response = client.post("/api/typeset/preview", json=body)
        assert response.status_code == 200, response.text
        assert response.headers["content-type"] == "image/png"
        return json.loads(response.headers["X-Typeset-Facts"])

    def test_renders_with_saved_settings_by_default(self, client):
        facts = self.facts(client)
        assert len(facts) == 5
        assert all(f["size"] > 0 for f in facts)

    def test_facts_header_survives_chinese(self, client):
        """HTTP headers are latin-1 and every sample here is Chinese, so the
        JSON has to go out ASCII-escaped."""
        response = client.post("/api/typeset/preview", json={})
        header = response.headers["X-Typeset-Facts"]
        header.encode("latin-1")  # would raise if the escaping regressed
        assert "住手" in self.facts(client)[0]["text"]

    def test_settings_change_the_result(self, client):
        base = [f["size"] for f in self.facts(client, bubble_inset=0.05)]
        tight = [f["size"] for f in self.facts(client, bubble_inset=0.30)]
        assert tight < base

    def test_min_size_reports_overflow(self, client):
        """The flag is the whole point of the setting -- it decides which
        pages land in needs_review."""
        facts = self.facts(client, min_size=60)
        assert any(f["overflow"] for f in facts)
        assert all(f["size"] >= 60 for f in facts)

    def test_custom_texts_are_used(self, client):
        facts = self.facts(client, texts=["你好世界"])
        assert [f["text"] for f in facts] == ["你好世界"]

    def test_custom_texts_are_bounded(self, client):
        """The facts ride in a header, which servers cap at a few KB."""
        facts = self.facts(client, texts=["很长" * 400] * 40)
        assert len(facts) <= 8
        assert all(len(f["text"]) <= 120 for f in facts)

    def test_dimensions_are_clamped(self, client):
        """An unbounded size would let one request allocate arbitrarily."""
        facts = self.facts(client, width=99999, height=99999)
        assert facts  # served rather than exploding

    def test_preview_does_not_leak_into_later_renders(self, client):
        from ctt.typeset import settings

        before = settings.active()
        self.facts(client, font="SourceHanSerifSC", min_size=50)
        assert settings.active() is before

    def test_fonts_report_availability(self, client):
        """A font name that does not resolve renders every glyph as a box, and
        the output image is a slow way to discover that."""
        body = client.get("/api/typeset/fonts").json()
        names = {f["name"] for f in body["fonts"]}
        assert body["default"] in names
        assert any(f["available"] for f in body["fonts"])
        assert all(f["file"] for f in body["fonts"] if f["available"])
