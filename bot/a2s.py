import socket

A2S_INFO = b"\xff\xff\xff\xffTSource Engine Query\x00"


def _read_cstring(data, offset):
    end = data.index(b"\x00", offset)
    return data[offset:end].decode("utf-8", "replace"), end + 1


def parse_info(data):
    if len(data) < 10 or data[4:5] != b"I":
        return None
    try:
        offset = 6
        name, offset = _read_cstring(data, offset)
        map_name, offset = _read_cstring(data, offset)
        _, offset = _read_cstring(data, offset)
        _, offset = _read_cstring(data, offset)
        offset += 2
        return {
            "name": name,
            "map": map_name,
            "players": data[offset],
            "max_players": data[offset + 1],
        }
    except (ValueError, IndexError):
        return None


def query(ip, port=27015, timeout=3.0):
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.settimeout(timeout)
    try:
        s.sendto(A2S_INFO, (ip, port))
        data, _ = s.recvfrom(4096)
        if data[4:5] == b"A":
            s.sendto(A2S_INFO + data[5:9], (ip, port))
            data, _ = s.recvfrom(4096)
        return parse_info(data)
    except (socket.timeout, OSError, IndexError):
        return None
    finally:
        s.close()
