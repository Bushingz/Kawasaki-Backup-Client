import socket
import re
import time
import datetime
from pathlib import Path
from typing import Callable, Optional, Tuple

__all__ = ['KawasakiBackupClient']

# --- Protocol constants ---
START_HEADER = b'\x05\x02B'               # Marks start of backup header
SECOND_HEADER = b'\x02B    0\x17'          # Secondary handshake header
ACK_BYTE = b'\x06'                          # Acknowledgement byte
DATA_REC_REGEX = re.compile(rb'(?:\x17)?\x05\x02D[^\r\n]*\r')
EOT_MARKER = b'\x05\x02E\x17'             # End-of-transfer marker
SAVELOAD_IN_PROGRESS = re.compile(rb'SAVE/LOAD in progress')

# Default chunk size for progress updates (10 KB)
DEFAULT_PROGRESS_INTERVAL = 10 * 1024  # bytes

class KawasakiBackupClient:
    """
    Perform backups from a Kawasaki robot controller via Telnet.

    Args:
      ip (str): Robot IP address.
      base_name (str): Base filename for saved backup (no extension).
      port (int): Telnet port (default=23).
      username (str): Login username (default='as').
      full (bool): True for full backup, False for program-only (.as).
      on_status (callable): Callback for status updates: fn(str).
      on_progress (callable): Callback for progress: fn(int bytes_written).
      on_error (callable): Callback on error: fn(Exception).
      on_complete (callable): Callback on completion: fn(Path out_file, Path debug_log).
      progress_interval (int): Bytes between on_progress calls (default=10KB).
      socket_timeout (float): Timeout for socket operations in seconds (default=5.0).
      recv_timeout (float): Timeout for recv-until operations in seconds (default=10.0).
      connect_retries (int): Number of connect attempts before failing (default=1).
      retry_delay (float): Seconds to wait between connect retries (default=1.0).

    Usage:
        client = KawasakiBackupClient(
            ip='10.0.0.1', base_name='backup1',
            on_status=print,
            on_progress=lambda b: print(f"{b//1024} KB"),
            connect_retries=3
        )
        client.run()
    """
    def __init__(
        self,
        ip: str,
        base_name: str,
        port: int = 23,
        username: str = 'as',
        full: bool = False,
        on_status: Optional[Callable[[str], None]] = None,
        on_progress: Optional[Callable[[int], None]] = None,
        on_error: Optional[Callable[[Exception], None]] = None,
        on_complete: Optional[Callable[[Path, Path], None]] = None,
        progress_interval: int = DEFAULT_PROGRESS_INTERVAL,
        socket_timeout: float = 5.0,
        recv_timeout: float = 10.0,
        connect_retries: int = 1,
        retry_delay: float = 1.0,
    ):
        # Connection settings
        self.ip = ip
        self.port = port
        self.username = username
        self.base_name = base_name
        self.full = full
        self.socket_timeout = socket_timeout
        self.recv_timeout = recv_timeout
        self.connect_retries = connect_retries
        self.retry_delay = retry_delay

        # Callbacks
        self.on_status = on_status
        self.on_progress = on_progress
        self.on_error = on_error
        self.on_complete = on_complete

        # Progress settings
        self.progress_interval = progress_interval

        # Cancellation flag
        self._cancel_requested = False

    def cancel(self):
        """
        Request cancellation of an in-progress backup. Safe to call from another thread.
        """
        self._cancel_requested = True
        self._emit_status("Backup cancellation requested.")

    def _emit_status(self, msg: str):
        if self.on_status:
            try:
                self.on_status(msg)
            except Exception:
                pass

    def _emit_progress(self, bytes_written: int):
        if self.on_progress:
            try:
                self.on_progress(bytes_written)
            except Exception:
                pass

    def _emit_error(self, exc: Exception):
        if self.on_error:
            try:
                self.on_error(exc)
            except Exception:
                pass

    def _emit_complete(self, out_path: Path, debug_path: Path):
        if self.on_complete:
            try:
                self.on_complete(out_path, debug_path)
            except Exception:
                pass

    def run(self) -> Tuple[Path, Path]:
        """
        Execute the backup process. Blocks until done or error.

        Returns:
            (out_path, debug_path)

        Raises:
            Exception on failure or cancellation.
        """
        # Prepare filenames
        safe_name = re.sub(r'[^A-Za-z0-9_.-]', '_', self.base_name)
        ext = 'as'
        out_path = Path(f"{safe_name}.{ext}")
        timestamp = datetime.datetime.now().strftime('%Y%m%d_%H%M%S')
        debug_path = Path(f"debug_{safe_name}_{timestamp}.log")

        # Command to send
        cmd = f"SAVE{'/Full' if self.full else ''} {safe_name}\r\n".encode()

        # Create socket
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(self.socket_timeout)

        # Attempt to connect with retries
        last_exc = None
        for attempt in range(1, self.connect_retries + 1):
            try:
                self._emit_status(f"Connecting to {self.ip}:{self.port} (attempt {attempt})...")
                sock.connect((self.ip, self.port))
                break
            except Exception as e:
                last_exc = e
                if attempt < self.connect_retries:
                    self._emit_status(f"Connect failed, retrying in {self.retry_delay}s...")
                    time.sleep(self.retry_delay)
                else:
                    self._emit_error(e)
                    raise

        try:
            self._emit_status("Connected. Waiting for login prompt...")
            with out_path.open('wb') as out_file, debug_path.open('wb') as dbg:
                # Login
                self._send_and_wait(
                    sock, f"{self.username}\r\n".encode(),
                    prompt_pattern=re.compile(rb'login:'), dbg=dbg,
                    timeout=self.recv_timeout,
                    status="Sending login credentials..."
                )
                # AUX prompt
                self._wait_for(
                    sock, pattern=re.compile(rb'AUX\d'), dbg=dbg,
                    timeout=self.recv_timeout,
                    status="Waiting for AUX prompt..."
                )
                # Send SAVE
                self._emit_status(f"Issuing SAVE command: {cmd.strip().decode()}")
                sock.sendall(cmd)
                # Wait for header or in-progress
                header_pattern = re.compile(
                    re.escape(START_HEADER + safe_name.encode() + b'.' + ext.encode() + b'\x17')
                )
                combined = re.compile(rb'(?:' + SAVELOAD_IN_PROGRESS.pattern + rb')|' +
                                      rb'(?:' + header_pattern.pattern + rb')')
                buf = self._recv_until(
                    sock, pattern=combined, dbg=dbg,
                    timeout=self.recv_timeout,
                    status="Waiting for header or in-progress message..."
                )
                if SAVELOAD_IN_PROGRESS.search(buf):
                    msg = "Another backup in progress; aborting."
                    self._emit_status(msg)
                    raise Exception(msg)
                # Handshake
                self._emit_status("Handshake: ACK + secondary header...")
                sock.sendall(ACK_BYTE)
                time.sleep(0.05)
                sock.sendall(SECOND_HEADER)
                # Stream data
                self._emit_status("Receiving data records...")
                total_written = 0
                last_report = 0
                buffer = bytearray()
                while True:
                    if self._cancel_requested:
                        raise Exception("Backup cancelled by user.")
                    try:
                        chunk = sock.recv(4096)
                    except socket.timeout:
                        chunk = b''
                    if chunk:
                        dbg.write(chunk)
                        buffer.extend(chunk)
                    # Parse records
                    while True:
                        m = DATA_REC_REGEX.search(buffer)
                        if not m:
                            break
                        rec = m.group(0)
                        payload = rec[4:] if rec.startswith(b'\x17') else rec[3:]
                        if payload.endswith(b'\r'):
                            payload = payload[:-1] + b'\r\n'
                        out_file.write(payload)
                        total_written += len(payload)
                        while total_written >= last_report + self.progress_interval:
                            last_report += self.progress_interval
                            self._emit_progress(last_report)
                        del buffer[:m.end()]
                    if EOT_MARKER in buffer:
                        break
                self._emit_status("Backup complete.")
                self._emit_complete(out_path, debug_path)
                return out_path, debug_path
        except Exception as e:
            self._emit_error(e)
            raise
        finally:
            try:
                sock.shutdown(socket.SHUT_RDWR)
            except Exception:
                pass
            sock.close()

    def _send_and_wait(
        self,
        sock: socket.socket,
        data: bytes,
        prompt_pattern: re.Pattern,
        dbg,
        timeout: float,
        status: str
    ) -> bytes:
        """
        Send data, then wait for prompt_pattern up to timeout seconds.
        """
        self._emit_status(status)
        sock.sendall(data)
        return self._recv_until(sock, pattern=prompt_pattern, dbg=dbg, timeout=timeout)

    def _wait_for(
        self,
        sock: socket.socket,
        pattern: re.Pattern,
        dbg,
        timeout: float,
        status: str
    ) -> bytes:
        """
        Wait for pattern up to timeout seconds.
        """
        self._emit_status(status)
        return self._recv_until(sock, pattern=pattern, dbg=dbg, timeout=timeout)

    def _recv_until(
        self,
        sock: socket.socket,
        pattern: re.Pattern,
        dbg,
        timeout: float = 10.0,
        status: Optional[str] = None
    ) -> bytes:
        """
        Read until pattern or timeout; write raw bytes to dbg.
        """
        if status:
            self._emit_status(status)
        sock.settimeout(timeout)
        buf = bytearray()
        while not pattern.search(buf):
            chunk = sock.recv(1024)
            if not chunk:
                break
            dbg.write(chunk)
            buf.extend(chunk)
        return bytes(buf)
