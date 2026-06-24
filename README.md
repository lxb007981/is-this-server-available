# Is This Server Available?

Find the first available server by running `npu-smi info` over SSH.

## Server CSV

Create a CSV file with these required headers:

```csv
ip,username,password
192.168.139.128,username_here,password_here
```

## Instalation

**Download [sshpass-win32](https://github.com/xhcoding/sshpass-win32/releases/latest) and put it in PATH.**

## Usage

```bash
python3 main.py --servers servers.csv
```

Optional SSH timeout:

```bash
python3 main.py --servers servers.csv --timeout 5
```

Run up to a specific number of SSH checks concurrently (defaults to 3):

```bash
python3 main.py --servers servers.csv --parallel 6
```

Check every server without stopping early, then report all available servers:

```bash
python3 main.py --servers servers.csv --all
```

Each run checks the servers in a random order, with no more than the configured
number of concurrent SSH connections. By default, the script stops as soon as it
finds a server whose `npu-smi info` output meets either availability condition:

- The output contains exactly 8 occurrences of
  `No running processes found in NPU`.

- The process table contains at least one process and every process name is
  exactly `VLLMWorker` `VLLMWorker_TP` or `VLLMWorker_DP`.

With `--all`, every server is checked and the available servers are displayed in
CSV order after all checks finish.

The SSH command is run in this form:

```bash
sshpass -p PASSWORD ssh -o StrictHostKeyChecking=no -o ConnectTimeout=TIMEOUT username@ip npu-smi info
```

## Exit Codes

- `0`: available server found
- `1`: all servers checked, none available
- `2`: setup or input error, such as missing `sshpass`, invalid CSV, or invalid options
