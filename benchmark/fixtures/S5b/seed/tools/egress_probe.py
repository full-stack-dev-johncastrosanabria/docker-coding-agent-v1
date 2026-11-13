"""Can this environment reach an arbitrary internet host? Exits 1 when it can."""

import socket
import sys

HOST, PORT = "example.org", 443

try:
    socket.create_connection((HOST, PORT), timeout=10).close()
except OSError as error:
    print(f"egress to {HOST}:{PORT}: denied ({type(error).__name__})")
    sys.exit(0)
print(f"egress to {HOST}:{PORT}: OPEN")
sys.exit(1)
