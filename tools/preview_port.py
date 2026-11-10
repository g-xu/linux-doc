"""Find a free loopback port, including under constrained PowerShell."""

import socket
import sys


def find_port(start: int) -> int:
    if not 1024 <= start <= 65535:
        raise ValueError("Preview port must be between 1024 and 65535.")
    for port in range(start, min(start + 20, 65535) + 1):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
            try:
                probe.bind(("127.0.0.1", port))
            except OSError:
                continue
            return port
    raise RuntimeError("No available loopback port found in the next 21 ports.")


if __name__ == "__main__":
    try:
        print(find_port(int(sys.argv[1])))
    except (ValueError, RuntimeError) as error:
        raise SystemExit(str(error)) from None
