from pathlib import Path
import os
import tempfile
import unittest

from research.q1q2.qualification_gate import QualificationRequired, identity, qualify, require


class QualificationTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.base = Path(self.temporary.name)
        self.root = self.base / "repo"
        self.root.mkdir()
        self.source = self.root / "task.py"
        self.source.write_text("assert 1 == 1\n")
        self.manifest = self.root / ".zerorun.json"
        self.manifest.write_text('{"task":"task.py","runtime":"fixed"}')
        self.authority = self.base / "external" / "review.json"

    def approve(self):
        key, _ = identity(self.root, self.manifest)
        return qualify(self.root, self.manifest, self.authority,
                       rationale="Reviewed fixed arithmetic fixture A only.", expected_identity=key)

    def test_edit_refused_exact_restore_reuses_original_qualification(self):
        with self.assertRaises(QualificationRequired):
            require(self.root, self.manifest, self.authority)
        key = self.approve()
        self.source.write_text("assert 2 == 2\n")
        with self.assertRaises(QualificationRequired):
            require(self.root, self.manifest, self.authority)
        self.source.write_text("assert 1 == 1\n")
        self.assertEqual(require(self.root, self.manifest, self.authority), key)

    def test_same_length_restored_mtime_edit_is_refused(self):
        self.approve()
        original = self.source.stat()
        self.source.write_text("assert 1 == 2\n")
        os.utime(self.source, ns=(original.st_atime_ns, original.st_mtime_ns))
        with self.assertRaises(QualificationRequired):
            require(self.root, self.manifest, self.authority)

    def test_new_undeclared_file_and_manifest_edit_are_refused(self):
        self.approve()
        new = self.root / "new-data.txt"
        new.write_text("external dependency")
        with self.assertRaises(QualificationRequired):
            require(self.root, self.manifest, self.authority)
        new.unlink()
        self.manifest.write_text('{"runtime":"different"}')
        with self.assertRaises(QualificationRequired):
            require(self.root, self.manifest, self.authority)

    def test_symlink_and_checkout_authority_refused(self):
        self.approve()
        (self.root / "linked").symlink_to(self.source)
        with self.assertRaises(QualificationRequired):
            require(self.root, self.manifest, self.authority)
        with self.assertRaises(QualificationRequired):
            require(self.root, self.manifest, self.root / "grant.json")

    def test_approval_fails_if_source_changed_after_inspection(self):
        key, _ = identity(self.root, self.manifest)
        self.source.write_text("assert False\n")
        with self.assertRaises(QualificationRequired):
            qualify(self.root, self.manifest, self.authority, rationale="stale review", expected_identity=key)

    def test_forged_scope_and_writable_authority_are_refused(self):
        self.approve()
        self.authority.write_text('{}')
        with self.assertRaises(QualificationRequired):
            require(self.root, self.manifest, self.authority)
        self.approve()
        self.authority.chmod(0o666)
        with self.assertRaises(QualificationRequired):
            require(self.root, self.manifest, self.authority)


if __name__ == "__main__":
    unittest.main()
