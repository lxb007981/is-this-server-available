# Is This Server Available?

Find the first server whose 8 NPUs are idle by running `npu-smi info` over SSH.

## Server CSV

Create a CSV file with these required headers:

```csv
ip,username,password
192.168.139.128,username_here,password_here
```

## Usage

```bash
python3 main.py --servers servers.csv
```

Optional SSH timeout:

```bash
python3 main.py --servers servers.csv --timeout 15
```

Each run checks the servers in a random order. The script stops as soon as it
finds a server whose `npu-smi info` output contains exactly 8 occurrences of:

```text
No running processes found in NPU
```

The SSH command is run in this form:

```bash
sshpass -p PASSWORD -k ssh -o ConnectTimeout=TIMEOUT username@ip npu-smi info
```

## Exit Codes

- `0`: available server found
- `1`: all servers checked, none available
- `2`: setup or input error, such as missing `sshpass`, invalid CSV, or invalid timeout
