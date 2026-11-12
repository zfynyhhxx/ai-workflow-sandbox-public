import unittest
from demo.labels import normalize_label

class LabelTests(unittest.TestCase):
    def test_normalize(self): self.assertEqual(normalize_label('  Hello   WORLD  '),'hello world')
    def test_unicode(self): self.assertEqual(normalize_label('  STRAẞE  '),'strasse')
    def test_empty(self):
        with self.assertRaises(ValueError): normalize_label(' \t\n ')
    def test_wrong_type(self):
        with self.assertRaises(TypeError): normalize_label(42)
