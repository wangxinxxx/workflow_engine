import unittest

from app.workflow.step_specs import load_step_specs


class StepSpecsTest(unittest.TestCase):
    def test_step_specs_have_unique_ids(self):
        specs = load_step_specs()
        self.assertTrue(specs)
        ids = [spec.id for spec in specs]
        self.assertEqual(len(ids), len(set(ids)))


if __name__ == "__main__":
    unittest.main()
