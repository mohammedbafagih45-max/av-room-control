"""Drivers for the matrix switcher and the audio DSP."""

from .base import DeviceError, TcpDriver


class SwitcherDriver(TcpDriver):
    def __init__(self, host: str, port: int, *, timeout: float = 2.0, name: str = "switcher"):
        super().__init__(name, host, port, timeout)

    async def _ask(self, command: str) -> list[str]:
        reply = await self.request(command)
        if reply.startswith("ERR"):
            raise DeviceError(f"{self.name}: {command!r} -> {reply}")
        return reply.split()

    async def route(self, source: int, output: int) -> None:
        await self._ask(f"ROUTE {source} {output}")

    async def get_route(self, output: int) -> int:
        return int((await self._ask(f"ROUTE? {output}"))[1])

    async def has_signal(self, source: int) -> bool:
        return (await self._ask(f"SIGNAL? {source}"))[2] == "1"


class DSPDriver(TcpDriver):
    def __init__(self, host: str, port: int, *, timeout: float = 2.0, name: str = "dsp"):
        super().__init__(name, host, port, timeout)

    async def _ask(self, command: str) -> str:
        reply = await self.request(command)
        if reply.startswith("ERR"):
            raise DeviceError(f"{self.name}: {command!r} -> {reply}")
        return reply.split()[1]

    async def set_volume(self, level: int) -> int:
        level = max(0, min(100, int(level)))
        return int(await self._ask(f"SET VOL {level}"))

    async def get_volume(self) -> int:
        return int(await self._ask("GET VOL"))

    async def set_mute(self, muted: bool) -> bool:
        return await self._ask(f"SET MUTE {int(muted)}") == "1"

    async def get_mute(self) -> bool:
        return await self._ask("GET MUTE") == "1"
