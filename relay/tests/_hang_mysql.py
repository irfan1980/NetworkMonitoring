import socket, struct, threading


def packet(seq, payload):
    return struct.pack("<I", len(payload))[:3] + bytes([seq]) + payload


def handle(conn):
    try:
        greeting = (b"\x0a" + b"5.7.0-fake\x00" + struct.pack("<I", 1) + b"abcdefgh" + b"\x00"
                    + struct.pack("<H", 0xF7FF) + b"\x21" + struct.pack("<H", 2) + struct.pack("<H", 0x81FF)
                    + b"\x15" + b"\x00" * 10 + b"ijklmnopqrst\x00" + b"mysql_native_password\x00")
        conn.sendall(packet(0, greeting))
        hdr = conn.recv(4)
        n = int.from_bytes(hdr[:3], "little")
        got = 0
        while got < n:
            got += len(conn.recv(n - got))
        conn.sendall(packet(2, b"\x00\x00\x00\x02\x00\x00\x00"))
        while True:
            data = conn.recv(4096)
            if not data:
                return
            pass
    except Exception:
        pass


def start(port: int) -> socket.socket:
    srv = socket.socket()
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind(("127.0.0.1", port))
    srv.listen(20)

    def loop():
        while True:
            try:
                c, _ = srv.accept()
            except OSError:
                return
            threading.Thread(target=handle, args=(c,), daemon=True).start()
    threading.Thread(target=loop, daemon=True).start()
    return srv
