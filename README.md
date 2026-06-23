# Is This Server Available?

Find the first server whose 8 NPUs are idle by running `npu-smi info` over SSH.

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

Each run checks the servers in a random order, with no more than the configured
number of concurrent SSH connections. The script stops as soon as it finds a
server whose `npu-smi info` output contains exactly 8 occurrences of:

```text
No running processes found in NPU
```

The SSH command is run in this form:

```bash
sshpass -p PASSWORD ssh -o StrictHostKeyChecking=no -o ConnectTimeout=TIMEOUT username@ip npu-smi info
```

## Exit Codes

- `0`: available server found
- `1`: all servers checked, none available
- `2`: setup or input error, such as missing `sshpass`, invalid CSV, or invalid options
