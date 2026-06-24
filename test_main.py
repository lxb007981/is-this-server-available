import argparse
import contextlib
import io
import subprocess
import unittest
from unittest import mock

import main


def make_process_output(*process_names):
    lines = [
        "+---------------------------+---------------+--------------------+",
        "| NPU     Chip              | Process id    | Process name             "
        "| Process memory(MB)      |",
        "+===========================+===============+====================+",
    ]
    lines.extend(
        f"| {index}       0                 | {1000 + index}          | {name:<24} | 1024                    |"
        for index, name in enumerate(process_names)
    )
    lines.append(
        "+===========================+===============+====================+"
    )
    return "\n".join(lines)


class AvailabilityTests(unittest.TestCase):
    def test_eight_idle_markers_are_available(self):
        output = "\n".join([main.IDLE_MARKER] * 8)

        self.assertTrue(main.is_available(output))
        self.assertFalse(main.is_available("\n".join([main.IDLE_MARKER] * 7)))
        self.assertFalse(main.is_available("\n".join([main.IDLE_MARKER] * 9)))

    def test_only_allowed_worker_processes_are_available(self):
        allowed_process_sets = [
            ("VLLMWorker_TP",),
            ("VLLMWorker_DP",),
            ("VLLMWorker_TP", "VLLMWorker_DP"),
        ]

        for process_names in allowed_process_sets:
            with self.subTest(process_names=process_names):
                self.assertTrue(main.is_available(make_process_output(*process_names)))

    def test_other_or_partial_process_name_is_not_available(self):
        disallowed_process_sets = [
            ("VLLMWorker_DP", "python"),
            ("VLLMWorker_extra",),
        ]

        for process_names in disallowed_process_sets:
            with self.subTest(process_names=process_names):
                self.assertFalse(main.is_available(make_process_output(*process_names)))

    def test_allowed_name_outside_process_table_is_not_available(self):
        self.assertFalse(main.is_available("diagnostic: VLLMWorker_DP"))

    def test_empty_or_malformed_process_table_is_not_available(self):
        self.assertFalse(main.is_available(make_process_output()))

        malformed_output = make_process_output("VLLMWorker_DP").replace(
            "| 0       0", "| invalid", 1
        )
        self.assertFalse(main.is_available(malformed_output))


class AllServersTests(unittest.TestCase):
    def setUp(self):
        self.servers = [
            main.Server("server-1", "user", "password"),
            main.Server("server-2", "user", "password"),
            main.Server("server-3", "user", "password"),
        ]

    @mock.patch("main.random.shuffle")
    @mock.patch("main.check_server")
    def test_checks_every_server_and_keeps_csv_report_order(
        self, check_server, _shuffle
    ):
        check_server.side_effect = [(False, ""), (True, ""), (False, "")]

        results = main.check_all_servers(self.servers, timeout=5, parallel=1)

        self.assertEqual([result.server for result in results], self.servers)
        self.assertEqual([result.available for result in results], [False, True, False])
        self.assertEqual(check_server.call_count, 3)

    @mock.patch("main.random.shuffle")
    @mock.patch("main.check_server")
    def test_includes_failed_checks_in_results(self, check_server, _shuffle):
        check_server.side_effect = subprocess.TimeoutExpired("ssh", 15)

        results = main.check_all_servers([self.servers[0]], timeout=5, parallel=1)

        self.assertIsNone(results[0].available)
        self.assertEqual(results[0].error, "timed out after 15 seconds")

    def test_report_displays_only_available_servers(self):
        results = [
            main.ServerResult(self.servers[0], False),
            main.ServerResult(self.servers[1], True),
            main.ServerResult(self.servers[2], None, "check failed"),
        ]

        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            main.print_all_results(results)

        self.assertEqual(output.getvalue(), "Available servers:\n  user@server-2\n")

    def test_report_says_when_no_server_is_available(self):
        results = [
            main.ServerResult(self.servers[0], False),
            main.ServerResult(self.servers[1], None, "check failed"),
        ]

        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            main.print_all_results(results)

        self.assertEqual(output.getvalue(), "No available server found.\n")

    @mock.patch("main.shutil.which", return_value="sshpass")
    @mock.patch("main.load_servers")
    @mock.patch("main.parse_args")
    @mock.patch("main.check_all_servers")
    def test_all_mode_exit_is_success_when_any_server_is_available(
        self, check_all, parse_args, load_servers, _which
    ):
        parse_args.return_value = argparse.Namespace(
            servers="servers.csv", timeout=5, parallel=3, all=True
        )
        load_servers.return_value = self.servers
        check_all.return_value = [
            main.ServerResult(self.servers[0], False),
            main.ServerResult(self.servers[1], True),
        ]

        with contextlib.redirect_stdout(io.StringIO()):
            exit_code = main.main()

        self.assertEqual(exit_code, main.EXIT_FOUND)


if __name__ == "__main__":
    unittest.main()
