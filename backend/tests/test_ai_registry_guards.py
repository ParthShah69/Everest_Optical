"""Keep every AI tool on the intended side of the approval boundary."""

import json
import os
import sys
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from services.ai_service import AIAssistant, READ_TOOLS, WRITE_TOOLS
from services.ai_tools import TOOLS


class AIRegistryGuardTests(unittest.TestCase):
    def test_every_tool_is_classified_once(self):
        registered = [tool.__name__ for tool in TOOLS]
        self.assertEqual(len(registered), len(set(registered)))
        self.assertFalse(READ_TOOLS & WRITE_TOOLS)
        self.assertEqual(set(registered), READ_TOOLS | WRITE_TOOLS)

    def test_every_write_tool_refuses_execution_without_approval(self):
        assistant = AIAssistant.__new__(AIAssistant)
        for tool_name in WRITE_TOOLS:
            with self.subTest(tool=tool_name):
                result = json.loads(assistant._execute_tool(tool_name, {}))
                self.assertFalse(result['success'])
                self.assertIn('approval', result['error'].lower())


if __name__ == '__main__':
    unittest.main()
