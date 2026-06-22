import argparse
import csv
import random
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Optional


IDLE_MARKER = "No running processes found in NPU"
REQUIRED_COLUMNS = ("ip", "username", "password")

EXIT_FOUND = 0
EXIT_NONE_AVAILABLE = 1
EXIT_ERROR = 2


@dataclass(frozen=True)
class Server:
    ip: str
    username: str
    password: str


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Find the first server whose 8 NPUs are idle."
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
        default=15,
        help="SSH connection timeout in seconds. Defaults to 15.",
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
        "-k",
        "ssh",
        "-o",
        f"ConnectTimeout={timeout}",
        f"{server.username}@{server.ip}",
        "npu-smi",
        "info",
    ]


def is_available(output: str) -> bool:
    return output.count(IDLE_MARKER) == 8


def check_server(server: Server, timeout: int) -> tuple[bool, str]:
    command = build_command(server, timeout)
    result = subprocess.run(
        command,
        capture_output=True,
        text=True,
        timeout=timeout + 5,
        check=False,
    )
    output = result.stdout + result.stderr
    return is_available(output), output


def find_available_server(servers: list[Server], timeout: int) -> Optional[Server]:
    random.shuffle(servers)

    for server in servers:
        print(f"Checking {server.username}@{server.ip} ...", flush=True)
        try:
            available, _output = check_server(server, timeout)
        except subprocess.TimeoutExpired:
            print(f"  skipped: timed out after {timeout + 5} seconds", flush=True)
            continue
        except OSError as error:
            print(f"  skipped: failed to run ssh command: {error}", flush=True)
            continue

        if available:
            return server

        print("  not available", flush=True)

    return None


def main() -> int:
    args = parse_args()

    if args.timeout <= 0:
        print("Error: --timeout must be greater than 0", file=sys.stderr)
        return EXIT_ERROR

    try:
        servers = load_servers(args.servers)
    except ValueError as error:
        print(f"Error: {error}", file=sys.stderr)
        return EXIT_ERROR

    if shutil.which("sshpass") is None:
        print("Error: sshpass is not installed or not on PATH", file=sys.stderr)
        return EXIT_ERROR

    server = find_available_server(servers, args.timeout)
    if server is None:
        print("No available server found.")
        return EXIT_NONE_AVAILABLE

    print(f"Available server found: {server.username}@{server.ip}")
    return EXIT_FOUND


if __name__ == "__main__":
    raise SystemExit(main())
