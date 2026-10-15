"""Base TCP driver: the controller side of a device connection.

This mirrors what a Crestron TCP/IP client (SIMPL "TCP/IP Client" symbol or
SIMPL# `TCPClient`) does for a third-party device:

* keep one persistent socket open,
* send one command at a time and wait for its reply (a lock serialises them),
* time out if the device goes quiet,
* drop and re-open the socket once on failure before declaring it offline.
"""

import asyncio
import logging


class DeviceError(Exception):
    """The device answered, but with an error."""


class DeviceOffline(DeviceError):
    """The device could not be reached."""


class TcpDriver:
    rx_eol = b"\n"  # byte that ends each reply from the device
    tx_eol = "\r\n"

    def __init__(self, name: str, host: str, port: int, timeout: float = 2.0):
        self.name = name
        self.host = host
        self.port = port
        self.timeout = timeout
        self.online = False
        self._reader: asyncio.StreamReader | None = None
        self._writer: asyncio.StreamWriter | None = None
        self._lock = asyncio.Lock()
        self.log = logging.getLogger(f"driver.{name}")

    async def _connect(self) -> None:
        self._reader, self._writer = await asyncio.wait_for(
            asyncio.open_connection(self.host, self.port), self.timeout)
        self.log.info("connected to %s:%s", self.host, self.port)
        await self.on_connected()

    async def on_connected(self) -> None:
        """Hook for protocols that send a greeting or need a login."""

    def frame(self, command: str) -> str:
        """Hook to decorate a command just before it is sent (e.g. auth)."""
        return command

    async def close(self) -> None:
        if self._writer:
            self._writer.close()
            try:
                await self._writer.wait_closed()
            except (ConnectionError, OSError):
                pass
        self._reader = self._writer = None

    async def readline(self) -> str:
        assert self._reader is not None
        raw = await asyncio.wait_for(self._reader.readuntil(self.rx_eol), self.timeout)
        return raw.decode(errors="replace").strip()

    async def request(self, command: str) -> str:
        """Send one command and return the device's reply line."""
        async with self._lock:
            for attempt in (1, 2):
                try:
                    if self._writer is None:
                        await self._connect()
                    wire = self.frame(command)
                    self.log.debug("TX %r", wire)
                    self._writer.write((wire + self.tx_eol).encode())
                    await self._writer.drain()
                    reply = await self.readline()
                    self.log.debug("RX %r", reply)
                    self.online = True
                    return reply
                except (OSError, asyncio.TimeoutError, asyncio.IncompleteReadError) as exc:
                    await self.close()
                    if attempt == 2:
                        if self.online:
                            self.log.warning("went offline: %s", exc or type(exc).__name__)
                        self.online = False
                        raise DeviceOffline(f"{self.name} not responding at {self.host}:{self.port}") from exc
        raise AssertionError("unreachable")
