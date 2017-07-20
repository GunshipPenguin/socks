import socket
import threading
import select
import sys
import struct
import ipaddress

HOST = '0.0.0.0'
PORT = 12345

BUFSIZE = 4096

BACKLOG = 10

# Timeout (in seconds) for SOCKS CONNECT reuquests after which CD_REQUEST_REJECTED will be returned
CONNECT_TIMEOUT = 20

# Version in server reply code (Should always be 0)
VN = 0x00

# Version number specified by clients when connecting (Should always be 4 for SOCKS4a)
CLIENT_VN = 0x04

REQUEST_TYPE_CONNECT = 0x01
REQUEST_TYPE_BIND = 0x02

CD_REQUEST_GRANTED = 90
CD_REQUEST_REJECTED = 91

class ClientRequest:
    def __init__(self, data):
        '''Construct a new client request from the given binary data'''
        self.invalid = False

        # Client requests must be at least 9 bytes to hold all necessary data
        if len(data) < 9:
            self.invalid = True
            return

        # Extract everything minus the userid from data
        vn, cd, dst_port, dst_ip = struct.unpack('>BBHL', data[:8])

        # Version number
        if (vn != CLIENT_VN):
            self.invalid = True

        # SOCKS command code (CD)
        self.cd = cd
        if (self.cd != REQUEST_TYPE_CONNECT and self.cd != REQUEST_TYPE_BIND):
            self.invalid = True

        # Destination port
        self.dst_port = dst_port

        # Destination IP (Parse as a dotted quad string)
        self.dst_ip = ipaddress.IPv4Address(dst_ip).exploded

        # UserId
        self.userid = data[8:-1] # Strip the null byte at the end

    def isInvalid(self):
        return self.invalid

class SocksProxy:

    def __init__(self, host, port, bufsize, backlog):
        self._host = host
        self._port = port
        self._bufsize = bufsize
        self._backlog = backlog

    def start(self):
        print ('Listening on ' + self._host + ':' + str(self._port))

        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        s.bind((self._host, self._port))
        s.listen(self._backlog)

        while True:
            try:
                conn, addr = s.accept()
                data = conn.recv(self._bufsize)

                # Got a connection, handle it with process_request()
                self._process_request(data, conn)
            except KeyboardInterrupt as ki:
                s.close()
                print('Caught KeyboardInterrupt, exiting')
                sys.exit(0)
            except Exception as e:
                print(e)
                s.close()
                sys.exit(1)

    def _build_reply(self, cd, dst_port=0x0000, dst_ip=0x000000):
        return struct.pack('>BBHL', VN, cd, dst_port, dst_ip)

    def _process_connect_request(self, clientRequest, clientConn):
        serverConn = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        serverConn.settimeout(CONNECT_TIMEOUT)

        try:
            serverConn.connect((clientRequest.dst_ip, clientRequest.dst_port))
        except socket.timeout:
            # Connection to specified host timed out, reject the SOCKS request
            serverConn.close()
            clientConn.send(self._build_reply(CD_REQUEST_REJECTED))
            clientConn.close()

        clientConn.send(self._build_reply(CD_REQUEST_GRANTED))

        forward = threading.Thread(target=self._forward_connection, args=(clientConn, serverConn))
        forward.daemon = True
        forward.start()

    def _process_bind_request(self, clientRequest, clientConn):
        # TODO: Impelement this
        clientConn.send(self._build_reply(CD_REQUEST_REJECTED))
        clientConn.close()

    def _process_request(self, data, clientConn):
        clientRequest = ClientRequest(data)

        # Handle invalid requests
        if clientRequest.isInvalid():
            clientConn.send(self._build_reply(CD_REQUEST_REJECTED))
            clientConn.close()
            return

        if clientRequest.cd == REQUEST_TYPE_CONNECT:
            self._process_connect_request(clientRequest, clientConn)
        else:
            self._process_bind_request(clientRequest, clientConn)

    def _close_all(self, socks):
        for s in socks:
            s.close()

    def _forward_connection(self, src, dest):
        while True:
            ready, _, err = select.select([src, dest], [], [src, dest])

            # Handle socket errors
            if err:
                _close_all([src, dest])
                return

            for s in ready:
                try:
                    data = s.recv(BUFSIZE)
                except ConnectionResetError:
                    # Connection reset by either src or dest, close sockets and return
                    _close_all([src, dest])
                    return

                if not data:
                    # Connection gracefully closed, close sockets and return
                    _close_all([src, dest])
                    return

                if s is src:
                    dest.sendall(data)
                else:
                    src.sendall(data)

if __name__ == '__main__':
    proxy = SocksProxy(HOST, PORT, BUFSIZE, BACKLOG)
    proxy.start()