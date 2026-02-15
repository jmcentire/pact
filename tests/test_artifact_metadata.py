"""Tests for artifact metadata (PBOM)."""
from pathlib import Path
from datetime import datetime

from pact.schemas import ArtifactMetadata
from pact.project import write_artifact_metadata, read_artifact_metadata


class TestArtifactMetadata:
    def test_metadata_creation(self):
        meta = ArtifactMetadata(
            model="claude-opus-4-6",
            component_id="my_comp",
            artifact_type="contract",
            cost_input_tokens=5000,
            cost_output_tokens=2000,
            cost_usd=0.225,
            timestamp=datetime.now().isoformat(),
            run_id="run-abc123",
        )
        assert meta.model == "claude-opus-4-6"
        assert meta.artifact_type == "contract"
        assert meta.cost_usd == 0.225

    def test_metadata_defaults(self):
        meta = ArtifactMetadata()
        assert meta.pact_version == "0.1.0"
        assert meta.model == ""
        assert meta.cost_usd == 0.0

    def test_metadata_json_roundtrip(self):
        meta = ArtifactMetadata(
            model="claude-opus-4-6",
            component_id="comp_a",
            artifact_type="implementation",
            run_id="run-xyz",
        )
        json_str = meta.model_dump_json()
        restored = ArtifactMetadata.model_validate_json(json_str)
        assert restored == meta

    def test_write_and_read(self, tmp_path):
        artifact = tmp_path / "contract.json"
        artifact.write_text("{}")

        meta = ArtifactMetadata(
            model="claude-opus-4-6",
            component_id="my_comp",
            artifact_type="contract",
            cost_input_tokens=1000,
            cost_output_tokens=500,
            cost_usd=0.05,
            timestamp=datetime.now().isoformat(),
            run_id="run-123",
        )
        write_artifact_metadata(artifact, meta)

        # Sidecar should exist
        sidecar = Path(str(artifact) + ".meta.json")
        assert sidecar.exists()

        # Read back
        restored = read_artifact_metadata(artifact)
        assert restored is not None
        assert restored.model == "claude-opus-4-6"
        assert restored.component_id == "my_comp"
        assert restored.cost_usd == 0.05
        assert restored.run_id == "run-123"

    def test_read_missing_returns_none(self, tmp_path):
        artifact = tmp_path / "nonexistent.json"
        assert read_artifact_metadata(artifact) is None

    def test_read_corrupt_returns_none(self, tmp_path):
        artifact = tmp_path / "contract.json"
        artifact.write_text("{}")
        sidecar = Path(str(artifact) + ".meta.json")
        sidecar.write_text("not valid json {{{")
        assert read_artifact_metadata(artifact) is None

    def test_write_creates_parent_dirs(self, tmp_path):
        artifact = tmp_path / "deep" / "nested" / "dir" / "contract.json"
        meta = ArtifactMetadata(component_id="deep_comp")
        write_artifact_metadata(artifact, meta)
        sidecar = Path(str(artifact) + ".meta.json")
        assert sidecar.exists()

    def test_metadata_contains_model(self, tmp_path):
        artifact = tmp_path / "impl.py"
        artifact.write_text("# code")
        meta = ArtifactMetadata(
            model="claude-sonnet-4-5-20250929",
            artifact_type="implementation",
        )
        write_artifact_metadata(artifact, meta)
        restored = read_artifact_metadata(artifact)
        assert restored.model == "claude-sonnet-4-5-20250929"

    def test_artifact_types(self):
        for at in ["contract", "test_suite", "implementation", "composition"]:
            meta = ArtifactMetadata(artifact_type=at)
            assert meta.artifact_type == at
