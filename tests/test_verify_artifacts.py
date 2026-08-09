import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from scripts import verify_artifacts


class ArtifactGateTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.specs = {
            "sample": verify_artifacts.PackageSpec(
                name="sample",
                binary="sample",
                architecture="amd64",
            )
        }
        self.config = {"public_url": "https://repo.invalid"}

    def tearDown(self):
        self.temporary.cleanup()

    def make_deb(self, filename: str, version: str, payload: str):
        source = self.root / f"source-{filename}"
        control = source / "DEBIAN"
        binary = source / "usr" / "bin" / "sample"
        control.mkdir(parents=True)
        binary.parent.mkdir(parents=True)
        (control / "control").write_text(
            "\n".join(
                [
                    "Package: sample",
                    f"Version: {version}",
                    "Architecture: amd64",
                    "Maintainer: Test <test@example.com>",
                    "Description: test package",
                    "",
                ]
            )
        )
        binary.write_text(payload)
        binary.chmod(0o755)
        destination = self.root / filename
        subprocess.run(
            ["dpkg-deb", "--build", str(source), str(destination)],
            check=True,
            capture_output=True,
            text=True,
        )
        return verify_artifacts.inspect_deb(destination)

    def assemble(self, candidate, production, fetch=None):
        destination = self.root / "publish"
        workdir = self.root / "work"
        workdir.mkdir(exist_ok=True)
        patch = mock.patch.object(verify_artifacts, "fetch", side_effect=fetch)
        with patch:
            return verify_artifacts.assemble_publish_set(
                {"sample": candidate},
                {"sample": production},
                self.specs,
                self.config,
                destination,
                workdir,
            )

    def test_rejects_version_downgrade(self):
        candidate = self.make_deb("candidate.deb", "1.0.0-1", "candidate")
        production = {
            "Package": "sample",
            "Version": "1.0.0-2",
            "Filename": "pool/sample.deb",
            "SHA256": "unused",
        }

        with self.assertRaisesRegex(ValueError, "older than production"):
            self.assemble(candidate, production)

    def test_uses_newer_candidate(self):
        candidate = self.make_deb("candidate.deb", "1.1.0-1", "candidate")
        production = {
            "Package": "sample",
            "Version": "1.0.0-2",
            "Filename": "pool/sample.deb",
            "SHA256": "unused",
        }

        result = self.assemble(candidate, production)

        self.assertEqual(result["sample"].version, "1.1.0-1")
        self.assertEqual(result["sample"].sha256, candidate.sha256)

    def test_preserves_published_bytes_for_equal_version(self):
        candidate = self.make_deb("candidate.deb", "1.0.0-2", "candidate")
        published = self.make_deb("published.deb", "1.0.0-2", "published")
        production = {
            "Package": "sample",
            "Version": "1.0.0-2",
            "Filename": "pool/published.deb",
            "SHA256": published.sha256,
        }

        def fetch(_url, destination):
            shutil.copy2(published.path, destination)

        result = self.assemble(candidate, production, fetch=fetch)

        self.assertEqual(result["sample"].version, "1.0.0-2")
        self.assertEqual(result["sample"].sha256, published.sha256)
        self.assertNotEqual(result["sample"].sha256, candidate.sha256)

    def test_control_parser_preserves_continuations(self):
        records = verify_artifacts.parse_control(
            "Package: sample\nDescription: first line\n second line\n\n"
        )

        self.assertEqual(records[0]["Package"], "sample")
        self.assertEqual(records[0]["Description"], "first line\n second line")


if __name__ == "__main__":
    unittest.main()
