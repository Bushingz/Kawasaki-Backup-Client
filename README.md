# KawasakiBackupClient

A Python library for performing backups of Kawasaki robot controllers over Telnet.\
It handles the full Telnet handshake, data streaming, error handling, and offers callbacks for status, progress, errors, and completion.

---

## Features

- **Simple API**: Single `run()` call performs the backup.
- **Callbacks**: Receive updates via `on_status`, `on_progress`, `on_error`, `on_complete`.
- **Cancellation**: Call `client.cancel()` from another thread to abort mid-transfer.
- **Custom Timeouts & Retries**: Configure `socket_timeout`, `recv_timeout`, `connect_retries`, and `retry_delay`.
- **Robust Parsing**: Handles records split across TCP packets, optional headers, and end-of-transfer detection.
- **Windows-Style Output**: Converts `CR` to `CRLF` in the saved file.
- **Debug Logging**: Captures raw Telnet traffic to a debug log.

---

## Installation

Simply copy `kawasaki_backup_client.py` into your project, or install it as a module:

```bash
git clone https://github.com/Bushingz/Kawasaki-Backup-Client.git
pip install path/to/kawasaki-backup-client
```

---

## Quick Start

```python
from kawasaki_backup_client import KawasakiBackupClient

# Define callbacks
def on_status(msg):
    print(f"[STATUS] {msg}")

def on_progress(bytes_written):
    print(f"[PROGRESS] {bytes_written//1024} KB written")

def on_error(exc):
    print(f"[ERROR] {exc}")

def on_complete(out_file, debug_log):
    print(f"Done: {out_file} (debug log: {debug_log})")

# Create client
client = KawasakiBackupClient(
    ip="10.0.0.1",
    base_name="robot1",
    full=False,
    on_status=on_status,
    on_progress=on_progress,
    on_error=on_error,
    on_complete=on_complete,
    socket_timeout=10.0,      # 10-second socket operations
    recv_timeout=15.0,        # 15-second recv-until operations
    connect_retries=3,
    retry_delay=2.0,
)

# Run backup
try:
    out_path, debug_path = client.run()
except Exception as e:
    print("Backup failed:", e)
```

---

## API Reference

### `KawasakiBackupClient(...)`

| Parameter           | Type       | Default | Description                                                       |
| ------------------- | ---------- | ------- | ----------------------------------------------------------------- |
| `ip`                | `str`      |         | IP address of the Kawasaki robot                                  |
| `base_name`         | `str`      |         | Base filename for the backup (no extension)                       |
| `port`              | `int`      | `23`    | Telnet port                                                       |
| `username`          | `str`      | `'as'`  | Login user                                                        |
| `full`              | `bool`     | `False` | If `True`, does a full backup; else program-only (`.as`) |
| `on_status`         | `callable` |         | `fn(str)` for status updates                                      |
| `on_progress`       | `callable` |         | `fn(int)` every `progress_interval` bytes written                 |
| `on_error`          | `callable` |         | `fn(Exception)` on any error                                      |
| `on_complete`       | `callable` |         | `fn(Path, Path)` when backup completes                            |
| `progress_interval` | `int`      | `10240` | Byte interval for progress callbacks (default 10 KB)              |
| `socket_timeout`    | `float`    | `5.0`   | Timeout for socket operations (seconds)                           |
| `recv_timeout`      | `float`    | `10.0`  | Timeout for read-until operations (seconds)                       |
| `connect_retries`   | `int`      | `1`     | Number of attempts to connect before failing                      |
| `retry_delay`       | `float`    | `1.0`   | Seconds to wait between connection retries                        |

### `run() -> (out_path, debug_path)`

Performs the backup. Blocks until complete, cancelled, or error.

- Raises an exception on failure or if `cancel()` was called.

### `cancel()`

Request cancellation. Safe to call from another thread; causes `run()` to abort.

---

## License

MIT License

---

*Happy automating!*

