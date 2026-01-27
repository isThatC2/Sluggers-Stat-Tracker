import dolphin_memory_engine as dme

class Data:
    def __init__(self, address, size):
        self.address = address
        self.size = size

def read_data(data: Data) -> bytes:
    return dme.read_bytes(data.address, data.size)

def write_data(data: Data, value: bytes):
    dme.write_bytes(data.address, value)
