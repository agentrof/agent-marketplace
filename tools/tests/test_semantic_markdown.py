import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "plugins/software-engineering-team/scripts"))

from ba_compile import without_generated_relations


class SemanticMarkdownTests(unittest.TestCase):
    def test_generated_relation_projection_is_semantically_inert(self):
        authored = "# Record\n\n## Navigation\n\n[[home|Home]]\n"
        rendered = (
            "# Record\n\n"
            "## Related knowledge <!-- sec: relations:generated:start -->\n\n"
            "- Used by: [[backlog/story|Story]]\n\n"
            "<!-- sec: relations:generated:end -->\n\n"
            "## Navigation\n\n[[home|Home]]\n"
        )
        self.assertEqual(without_generated_relations(rendered), authored)

    def test_incomplete_marker_is_not_treated_as_generated_content(self):
        partial = "# Record\n\n## Related knowledge <!-- sec: relations:generated:start -->\n"
        self.assertEqual(without_generated_relations(partial), partial)


if __name__ == "__main__":
    unittest.main()
