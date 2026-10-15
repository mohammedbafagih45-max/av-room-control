"""Base class for simulated AV devices.

Each simulated device is a small asyncio TCP server that speaks a line-based
control protocol, the same way real projectors, switchers and DSPs do when a
control processor (Crestron, Extron, AMX, Q-SYS...) talks to them over IP.
"""

import asyncio
import logging


class LineDevice:
    """A TCP server that reads commands line by line and writes replies.

    Subclasses implement `handle_line()` and, optionally, `greeting()`.
    Incoming commands may end in CR, LF or CR+LF, since real control systems
    are inconsistent about this.
    """

    name = "device"
    reply_eol = "\r\n"

    def __init__(self, host: str = "127.0.0.1", port: int = 0):
        self.host = host
        self.port = port
        self._server: asyncio.base_events.Server | None = None
        self.log = logging.getLogger(f"sim.{self.name}")

    # --- lifecycle -------------------------------------------------------
    async def start(self) -> None:
        self._server = await asyncio.start_server(self._handle, self.host, self.port)
        # When port=0 the OS picks a free port; record which one we got.
        self.port = self._server.sockets[0].getsockname()[1]
        self.log.info("listening on %s:%s", self.host, self.port)

    async def stop(self) -> None:
        if self._server:
            self._server.close()
            await self._server.wait_closed()
            self._server = None
            self.log.info("stopped")

    @property
    def running(self) -> bool:
        return self._server is not None

    # --- protocol hooks --------------------------------------------------
    def greeting(self, session: dict) -> str | None:
        """Text sent as soon as a client connects (None = send nothing)."""
        return None

    def handle_line(self, line: str, session: dict) -> str | None:
        """Return the reply for one command (None = no reply)."""
        raise NotImplementedError

    # --- connection handling ---------------------------------------------
    async def _handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
        peer = writer.get_extra_info("peername")
        session: dict = {"close": False}
        self.log.debug("client connected: %s", peer)
        try:
            hello = self.greeting(session)
            if hello is not None:
                writer.write((hello + self.reply_eol).encode())
                await writer.drain()

            buffer = b""
            while not session["close"]:
                chunk = await reader.read(1024)
                if not chunk:
                    break
                buffer += chunk.replace(b"\r\n", b"\n").replace(b"\r", b"\n")
                while b"\n" in buffer and not session["close"]:
                    raw, buffer = buffer.split(b"\n", 1)
                    line = raw.decode(errors="replace").strip()
                    if not line:
                        continue
                    self.log.debug("RX %r", line)
                    reply = self.handle_line(line, session)
                    if reply is not None:
                        self.log.debug("TX %r", reply)
                        writer.write((reply + self.reply_eol).encode())
                        await writer.drain()
        except (ConnectionError, asyncio.CancelledError):
            pass
        finally:
            writer.close()
            try:
                await writer.wait_closed()
            except (ConnectionError, OSError):
                pass
            self.log.debug("client disconnected: %s", peer)
