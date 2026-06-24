import argparse
import concurrent.futures
import csv
import random
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Optional


IDLE_MARKER = "No running processes found in NPU"
ALLOWED_PROCESS_NAMES = frozenset({"VLLMWorker", "VLLMWorker_TP", "VLLMWorker_DP"})
PROCESS_TABLE_HEADER = (
    "NPU Chip",
    "Process id",
    "Process name",
    "Process memory(MB)",
)
REQUIRED_COLUMNS = ("ip", "username", "password")

EXIT_FOUND = 0
EXIT_NONE_AVAILABLE = 1
EXIT_ERROR = 2


@dataclass(frozen=True)
class Server:
    ip: str
    username: str
    password: str


@dataclass(frozen=True)
class ServerResult:
    server: Server
    available: Optional[bool]
    error: Optional[str] = None


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Find the first available server using npu-smi process status."
    )
    parser.add_argument(
        "-s",
        "--servers",
        required=True,
        type=Path,
        help="CSV file with ip, username, and password columns.",
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=5,
        help="SSH connection timeout in seconds. Defaults to 5.",
    )
    parser.add_argument(
        "-p",
        "--parallel",
        type=int,
        default=3,
        help="Maximum number of concurrent SSH connections. Defaults to 3.",
    )
    parser.add_argument(
        "--all",
        action="store_true",
        help="Check every server and report all results after the checks finish.",
    )
    return parser.parse_args()


def load_servers(csv_path: Path) -> list[Server]:
    if not csv_path.exists():
        raise ValueError(f"server CSV does not exist: {csv_path}")
    if not csv_path.is_file():
        raise ValueError(f"server CSV path is not a file: {csv_path}")

    with csv_path.open(newline="") as csv_file:
        reader = csv.DictReader(csv_file)
        fieldnames = reader.fieldnames or []
        missing_columns = [column for column in REQUIRED_COLUMNS if column not in fieldnames]
        if missing_columns:
            columns = ", ".join(missing_columns)
            raise ValueError(f"server CSV is missing required column(s): {columns}")

        servers = []
        for row_number, row in enumerate(reader, start=2):
            ip = (row.get("ip") or "").strip()
            username = (row.get("username") or "").strip()
            password = row.get("password") or ""
            if not ip or not username or not password:
                raise ValueError(
                    f"server CSV row {row_number} must include ip, username, and password"
                )
            servers.append(Server(ip=ip, username=username, password=password))

    if not servers:
        raise ValueError("server CSV does not contain any server rows")

    return servers


def build_command(server: Server, timeout: int) -> list[str]:
    return [
        "sshpass",
        "-p",
        server.password,
        "ssh",
        "-o",
        "StrictHostKeyChecking=no",
        "-o",
        f"ConnectTimeout={timeout}",
        f"{server.username}@{server.ip}",
        "npu-smi",
        "info",
    ]


def _extract_process_names(output: str) -> Optional[list[str]]:
    process_names: list[str] = []
    in_process_table = False

    for line in output.splitlines():
        stripped_line = line.strip()
        cells = [cell.strip() for cell in stripped_line.strip("|").split("|")]
        normalized_cells = tuple(" ".join(cell.split()) for cell in cells)

        if normalized_cells == PROCESS_TABLE_HEADER:
            in_process_table = True
            continue

        if not in_process_table or not stripped_line:
            continue

        if stripped_line.startswith("+") and set(stripped_line) <= {"+", "-", "="}:
            continue

        # Ignore diagnostics appended after the table, but fail closed for malformed
        # pipe-delimited rows that appear to belong to the process table.
        if not (stripped_line.startswith("|") and stripped_line.endswith("|")):
            continue
        if len(cells) != 4:
            return None

        npu_and_chip = cells[0].split()
        if (
            len(npu_and_chip) != 2
            or not all(value.isdigit() for value in npu_and_chip)
            or not cells[1].isdigit()
            or not cells[3].isdigit()
            or not cells[2]
        ):
            return None

        process_names.append(cells[2])

    if not in_process_table:
        return None
    return process_names


def is_available(output: str) -> bool:
    if output.count(IDLE_MARKER) == 8:
        return True

    process_names = _extract_process_names(output)
    return bool(process_names) and all(
        process_name in ALLOWED_PROCESS_NAMES for process_name in process_names
    )


def check_server(server: Server, timeout: int) -> tuple[bool, str]:
    print(f"Checking {server.username}@{server.ip} ...", flush=True)
    command = build_command(server, timeout)
    result = subprocess.run(
        command,
        capture_output=True,
        text=True,
        timeout=timeout + 10, # covers sshpass, authentication, SSH startup, and remote command execution
        check=False,
    )
    output = result.stdout + result.stderr
    return is_available(output), output


def find_available_server(
    servers: list[Server], timeout: int, parallel: int = 3
) -> Optional[Server]:
    # Avoid consistently favoring servers that appear first in the CSV.
    random.shuffle(servers)

    with concurrent.futures.ThreadPoolExecutor(max_workers=parallel) as executor:
        futures = {}
        for server in servers:
            future = executor.submit(check_server, server, timeout)
            futures[future] = server

        # Process checks as they finish so a fast available server can be returned early.
        for future in concurrent.futures.as_completed(futures):
            server = futures[future]
            try:
                available, _output = future.result()
            except subprocess.TimeoutExpired:
                print(
                    f"  {server.username}@{server.ip} skipped: "
                    f"timed out after {timeout + 10} seconds",
                    flush=True,
                )
                continue
            except OSError as error:
                print(
                    f"  {server.username}@{server.ip} skipped: "
                    f"failed to run ssh command: {error}",
                    flush=True,
                )
                continue

            if available:
                # Cancellation is best-effort: checks that are already running will finish.
                for pending_future in futures:
                    pending_future.cancel()
                return server

            print(f"  {server.username}@{server.ip} not available", flush=True)

    return None


def check_all_servers(
    servers: list[Server], timeout: int, parallel: int = 3
) -> list[ServerResult]:
    # Keep the final report in CSV order while still querying in a random order.
    indexed_servers = list(enumerate(servers))
    random.shuffle(indexed_servers)
    results: list[Optional[ServerResult]] = [None] * len(servers)

    with concurrent.futures.ThreadPoolExecutor(max_workers=parallel) as executor:
        futures = {
            executor.submit(check_server, server, timeout): (index, server)
            for index, server in indexed_servers
        }

        for future in concurrent.futures.as_completed(futures):
            index, server = futures[future]
            try:
                available, _output = future.result()
                results[index] = ServerResult(server, available)
            except subprocess.TimeoutExpired:
                results[index] = ServerResult(
                    server,
                    None,
                    f"timed out after {timeout + 10} seconds",
                )
            except OSError as error:
                results[index] = ServerResult(
                    server,
                    None,
                    f"failed to run ssh command: {error}",
                )

    # Every submitted future is processed above, so all entries are populated.
    return [result for result in results if result is not None]


def print_all_results(results: list[ServerResult]) -> None:
    available_servers = [result.server for result in results if result.available]
    if not available_servers:
        print("No available server found.")
        return

    print("Available servers:")
    for server in available_servers:
        print(f"  {server.username}@{server.ip}")


def main() -> int:
    args = parse_args()

    if args.timeout <= 0:
        print("Error: --timeout must be greater than 0", file=sys.stderr)
        return EXIT_ERROR
    if args.parallel <= 0:
        print("Error: --parallel must be greater than 0", file=sys.stderr)
        return EXIT_ERROR

    try:
        servers = load_servers(args.servers)
    except ValueError as error:
        print(f"Error: {error}", file=sys.stderr)
        return EXIT_ERROR

    if shutil.which("sshpass") is None:
        print("Error: sshpass is not installed or not on PATH", file=sys.stderr)
        return EXIT_ERROR

    if args.all:
        results = check_all_servers(servers, args.timeout, args.parallel)
        print_all_results(results)
        if any(result.available for result in results):
            return EXIT_FOUND
        return EXIT_NONE_AVAILABLE

    server = find_available_server(servers, args.timeout, args.parallel)
    if server is None:
        print("No available server found.")
        return EXIT_NONE_AVAILABLE

    print(f"Available server found: {server.username}@{server.ip}")
    return EXIT_FOUND


if __name__ == "__main__":
    raise SystemExit(main())
