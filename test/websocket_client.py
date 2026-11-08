import base64
import hashlib
import json
import os
import socket
import struct
import urllib.parse


class WebSocket:
    def __init__(self, url, path, receive_buffer=None, origin=True):
        parsed=urllib.parse.urlsplit(url)
        if receive_buffer:
            self.socket=socket.socket(socket.AF_INET,socket.SOCK_STREAM)
            self.socket.settimeout(5)
            self.socket.setsockopt(socket.SOL_SOCKET,socket.SO_RCVBUF,receive_buffer)
            self.socket.connect((parsed.hostname,parsed.port))
        else:
            self.socket=socket.create_connection((parsed.hostname,parsed.port),timeout=5)
        key=base64.b64encode(os.urandom(16)).decode()
        headers=[f'GET {path} HTTP/1.1',f'Host: {parsed.netloc}','Upgrade: websocket','Connection: Upgrade',f'Sec-WebSocket-Key: {key}','Sec-WebSocket-Version: 13']
        if origin:headers.append(f'Origin: {url}')
        self.socket.sendall(('\r\n'.join(headers)+'\r\n\r\n').encode())
        response=bytearray()
        while not response.endswith(b'\r\n\r\n'):
            chunk=self.socket.recv(1)
            if not chunk:raise EOFError('Upgrade response ended early')
            response.extend(chunk)
            if len(response)>8192:raise AssertionError('Oversized upgrade response')
        self.status=int(response.split(b' ',2)[1])
        if self.status==101:
            expected=base64.b64encode(hashlib.sha1((key+'258EAFA5-E914-47DA-95CA-C5AB0DC85B11').encode()).digest())
            assert next(line.split(b':',1)[1].strip() for line in response.split(b'\r\n') if line.lower().startswith(b'sec-websocket-accept:'))==expected

    def send(self, value, opcode=1):
        data=json.dumps(value).encode() if not isinstance(value,bytes) else value
        mask=os.urandom(4)
        header=bytes([0x80|opcode])
        length=len(data)
        if length<126:header+=bytes([0x80|length])
        elif length<65536:header+=bytes([0x80|126])+struct.pack('!H',length)
        else:header+=bytes([0x80|127])+struct.pack('!Q',length)
        self.socket.sendall(header+mask+bytes(value^mask[index%4] for index,value in enumerate(data)))

    def exact(self, length):
        result=bytearray()
        while len(result)<length:
            chunk=self.socket.recv(length-len(result))
            if not chunk:raise EOFError('WebSocket closed')
            result.extend(chunk)
        return bytes(result)

    def receive(self):
        first,second=self.exact(2)
        assert not second&0x80
        length=second&127
        if length==126:length=struct.unpack('!H',self.exact(2))[0]
        elif length==127:length=struct.unpack('!Q',self.exact(8))[0]
        assert length<=32*1024*1024
        payload=self.exact(length)
        opcode=first&15
        if opcode==9:
            self.send(payload,opcode=10)
            return self.receive()
        return opcode,payload

    def ready(self):
        for _ in range(100):
            opcode,payload=self.receive()
            if opcode==1:
                value=json.loads(payload)
                if value.get('error'):raise AssertionError(value)
                if value.get('ready'):return
            if opcode==8:raise AssertionError(payload)
        raise AssertionError('Missing attachment-ready frame')

    def close(self):
        try:
            if self.status==101:self.send(struct.pack('!H',1000),opcode=8)
        except OSError:pass
        self.socket.close()
