"""PJLink Class 1 driver.

Works against the simulator and against real PJLink projectors (Epson,
NEC/Sharp, Panasonic, Sony, Hitachi/Maxell, Christie, Optoma and many more).
Default port is 4352. Set a password if the projector has PJLink auth on.
"""

import hashlib

from .base import DeviceError, TcpDriver

POWER_STATES = {"0": "off", "1": "on", "2": "cooling", "3": "warming"}

ERRORS = {
    "ERR1": "undefined command",
    "ERR2": "parameter out of range",
    "ERR3": "unavailable right now (warming up, cooling down or off)",
    "ERR4": "projector failure",
}

INPUTS = {"rgb1": "11", "rgb2": "12", "hdmi1": "31", "hdmi2": "32", "hdmi3": "33"}


class PJLinkError(DeviceError):
    def __init__(self, command: str, code: str):
        self.code = code
        super().__init__(f"{command}: {code} ({ERRORS.get(code, 'unknown error')})")


class PJLinkDriver(TcpDriver):
    rx_eol = b"\r"
    tx_eol = "\r"

    def __init__(self, host: str, port: int = 4352, *, password: str | None = None,
                 timeout: float = 2.0, name: str = "projector"):
        super().__init__(name, host, port, timeout)
        self.password = password
        self._auth_prefix = ""

    async def on_connected(self) -> None:
        greeting = await self.readline()
        if greeting.startswith("PJLINK 0"):
            self._auth_prefix = ""
        elif greeting.startswith("PJLINK 1"):
            if not self.password:
                raise DeviceError("projector requires a PJLink password")
            seed = greeting.split()[2]
            # The digest goes in front of the first command on this connection.
            self._auth_prefix = hashlib.md5((seed + self.password).encode()).hexdigest()
        else:
            raise DeviceError(f"unexpected PJLink greeting: {greeting!r}")

    def frame(self, command: str) -> str:
        # The auth digest is only known after connecting, so add it here.
        prefix, self._auth_prefix = self._auth_prefix, ""
        return prefix + command

    async def command(self, body: str) -> str:
        """Send e.g. 'POWR 1' and return the value after '='."""
        reply = await self.request(f"%1{body}")
        if reply == "PJLINK ERRA":
            await self.close()
            raise DeviceError("PJLink authentication failed (check password)")
        _, _, value = reply.partition("=")
        if value.startswith("ERR"):
            raise PJLinkError(body.split()[0], value)
        return value

    # --- friendly API ----------------------------------------------------
    async def power_on(self) -> None:
        await self.command("POWR 1")

    async def power_off(self) -> None:
        await self.command("POWR 0")

    async def get_power(self) -> str:
        return POWER_STATES.get(await self.command("POWR ?"), "unknown")

    async def set_input(self, code: str) -> None:
        await self.command(f"INPT {INPUTS.get(code, code)}")

    async def get_input(self) -> str:
        return await self.command("INPT ?")

    async def set_av_mute(self, muted: bool) -> None:
        await self.command(f"AVMT {'31' if muted else '30'}")

    async def get_lamp_hours(self) -> int:
        return int((await self.command("LAMP ?")).split()[0])

    async def get_name(self) -> str:
        return await self.command("NAME ?")
