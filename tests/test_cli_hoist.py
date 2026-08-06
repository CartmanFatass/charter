import unittest

from charter import cli


class TestFlagHoist(unittest.TestCase):
    def _parse(self, argv):
        return cli.build_parser().parse_args(cli._hoist_persona_memory(argv))

    def test_flags_trailing(self):
        ns = self._parse(["persona", "remember", "dev", "fact", "--shared"])
        self.assertEqual((ns.name, ns.text, ns.shared), ("dev", "fact", True))

    def test_flag_between_name_and_text(self):
        ns = self._parse(["persona", "remember", "dev", "--shared", "fact"])
        self.assertEqual((ns.name, ns.text, ns.shared), ("dev", "fact", True))

    def test_flags_first(self):
        ns = self._parse(["persona", "remember", "--ephemeral", "dev", "fact"])
        self.assertEqual((ns.name, ns.text, ns.ephemeral), ("dev", "fact", True))

    def test_value_flag_anywhere(self):
        ns = self._parse(["persona", "remember", "--title", "My Title", "dev", "fact"])
        self.assertEqual((ns.name, ns.text, ns.title), ("dev", "fact", "My Title"))

    def test_name_omitted_uses_text_only(self):
        ns = self._parse(["persona", "remember", "just some text"])
        self.assertEqual((ns.name, ns.text), (None, "just some text"))

    def test_dash_led_text_preserved(self):
        # free text starting with '-' (and containing a space) stays a positional
        ns = self._parse(["persona", "remember", "dev", "-> arrow note kept"])
        self.assertEqual((ns.name, ns.text), ("dev", "-> arrow note kept"))

    def test_log_value_flag_first(self):
        ns = self._parse(["persona", "log", "-n", "3", "dev"])
        self.assertEqual((ns.name, ns.n), ("dev", 3))

    def test_non_memory_command_untouched(self):
        argv = ["persona", "create", "x", "--role", "Y"]
        self.assertEqual(cli._hoist_persona_memory(argv), argv)

    def test_non_persona_command_untouched(self):
        argv = ["clone", "iam-service", "--workspace", "w"]
        self.assertEqual(cli._hoist_persona_memory(argv), argv)


if __name__ == "__main__":
    unittest.main()
