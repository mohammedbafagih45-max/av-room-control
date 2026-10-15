"""Room controller: the "program" that would run on the Crestron processor.

It owns the device drivers, runs the room macros (System On, System Off,
source select, volume), polls the devices for feedback, and exposes state
both as plain JSON and as a Crestron-style join table.
"""

import asyncio
import logging
import time
from collections import deque
from dataclasses import dataclass, field

from . import joins as J
from .drivers.ascii_devices import DSPDriver, SwitcherDriver
from .drivers.base import DeviceError
from .drivers.pjlink import PJLinkDriver

log = logging.getLogger("room")


@dataclass
class Source:
    input: int
    name: str


@dataclass
class RoomState:
    room_name: str = "Conference Room"
    system: str = "off"          # off | warming | on | cooling
    projector_power: str = "unknown"
    source: int | None = None
    volume: int = 0
    muted: bool = False
    lamp_hours: int | None = None
    status: str = "Ready"
    busy: bool = False
    online: dict = field(default_factory=lambda: {"projector": False, "switcher": False, "dsp": False})


class Room:
    PROJECTOR_INPUT = "31"   # switcher output 1 feeds projector HDMI 1
    VOLUME_STEP = 5

    def __init__(self, projector: PJLinkDriver, switcher: SwitcherDriver, dsp: DSPDriver,
                 sources: list[Source], *, room_name="Conference Room", default_source=1,
                 default_volume=40, warmup_timeout=60.0, poll_interval=2.0):
        self.projector = projector
        self.switcher = switcher
        self.dsp = dsp
        self.sources = {s.input: s for s in sources}
        self.default_source = default_source
        self.default_volume = default_volume
        self.warmup_timeout = warmup_timeout
        self.poll_interval = poll_interval
        self.state = RoomState(room_name=room_name)
        self.events: deque[dict] = deque(maxlen=60)
        self._macro: asyncio.Task | None = None

    # --- helpers ---------------------------------------------------------
    def _event(self, level: str, message: str) -> None:
        self.events.appendleft({"t": time.strftime("%H:%M:%S"), "level": level, "msg": message})
        getattr(log, "warning" if level == "error" else "info")(message)

    def _status(self, text: str) -> None:
        self.state.status = text
        self._event("info", text)

    async def _step(self, description: str, coro) -> bool:
        """Run one macro step. A failed device is logged but doesn't abort
        the whole macro: the rest of the room should still come up."""
        try:
            await coro
            return True
        except DeviceError as exc:
            self._event("error", f"{description} failed: {exc}")
            return False

    def _refresh_online(self) -> None:
        self.state.online = {
            "projector": self.projector.online,
            "switcher": self.switcher.online,
            "dsp": self.dsp.online,
        }

    # --- macros ----------------------------------------------------------
    async def system_on(self, source: int | None = None) -> None:
        source = source or self.default_source
        self.state.busy = True
        self.state.system = "warming"
        self._status("System On: starting")
        try:
            # Mute first so nobody gets a pop through the speakers.
            await self._step("Mute audio", self.dsp.set_mute(True))
            if await self._step("Projector power on", self.projector.power_on()):
                self._status("Projector warming up")
                await self._wait_for_power("on")
            await self._step("Projector input", self.projector.set_input(self.PROJECTOR_INPUT))
            await self._route(source)
            await self._step("Set volume", self.dsp.set_volume(self.default_volume))
            await self._step("Unmute audio", self.dsp.set_mute(False))
            self.state.volume, self.state.muted = self.default_volume, False
            self.state.system = "on"
            self._status("System On: ready")
        finally:
            self.state.busy = False
            self._refresh_online()

    async def system_off(self) -> None:
        self.state.busy = True
        self.state.system = "cooling"
        self._status("System Off: shutting down")
        try:
            await self._step("Mute audio", self.dsp.set_mute(True))
            self.state.muted = True
            if await self._step("Projector power off", self.projector.power_off()):
                self._status("Projector cooling down")
                await self._wait_for_power("off")
            self.state.system = "off"
            self.state.source = None
            self._status("System Off: done")
        finally:
            self.state.busy = False
            self._refresh_online()

    async def _wait_for_power(self, target: str) -> bool:
        deadline = time.monotonic() + self.warmup_timeout
        while time.monotonic() < deadline:
            try:
                self.state.projector_power = await self.projector.get_power()
            except DeviceError as exc:
                self._event("error", f"Projector poll failed: {exc}")
                return False
            if self.state.projector_power == target:
                return True
            await asyncio.sleep(0.5)
        self._event("error", f"Projector did not reach '{target}' within {self.warmup_timeout:.0f}s")
        return False

    async def _route(self, source: int) -> None:
        ok1 = await self._step(f"Route input {source} to projector", self.switcher.route(source, 1))
        await self._step(f"Route input {source} to confidence monitor", self.switcher.route(source, 2))
        if ok1:
            self.state.source = source

    async def select_source(self, source: int) -> None:
        if source not in self.sources:
            self._event("error", f"Unknown source {source}")
            return
        if self.state.system in ("off", "cooling"):
            # Pressing a source on a dark room turns the room on, like a real panel.
            await self.system_on(source)
            return
        await self._route(source)
        self._status(f"Source: {self.sources[source].name}")

    async def volume_step(self, direction: int) -> None:
        target = self.state.volume + direction * self.VOLUME_STEP
        await self.set_volume(target)

    async def set_volume(self, level: int) -> None:
        try:
            self.state.volume = await self.dsp.set_volume(level)
            if self.state.muted:
                self.state.muted = await self.dsp.set_mute(False)
        except DeviceError as exc:
            self._event("error", f"Volume change failed: {exc}")

    async def toggle_mute(self) -> None:
        try:
            self.state.muted = await self.dsp.set_mute(not self.state.muted)
            self._event("info", "Audio muted" if self.state.muted else "Audio unmuted")
        except DeviceError as exc:
            self._event("error", f"Mute failed: {exc}")

    # --- entry point for panels ------------------------------------------
    async def handle_action(self, action: str, value=None) -> str:
        """Dispatch a button press. Long macros run in the background so the
        panel stays responsive (and a second press doesn't stack macros)."""
        long_running = {"system_on", "system_off"}
        if action in long_running or action == "source":
            if self._macro and not self._macro.done():
                return "busy"
        if action == "system_on":
            self._macro = asyncio.create_task(self.system_on())
        elif action == "system_off":
            self._macro = asyncio.create_task(self.system_off())
        elif action == "source":
            self._macro = asyncio.create_task(self.select_source(int(value)))
        elif action == "vol_up":
            await self.volume_step(+1)
        elif action == "vol_down":
            await self.volume_step(-1)
        elif action == "volume":
            await self.set_volume(int(value))
        elif action == "mute":
            await self.toggle_mute()
        else:
            return "unknown action"
        return "ok"

    # --- feedback polling ------------------------------------------------
    async def poll_once(self) -> None:
        try:
            power = await self.projector.get_power()
            self.state.projector_power = power
            if not self.state.busy:
                # Keep in sync if someone used the projector remote.
                self.state.system = {"on": "on", "off": "off", "warming": "warming",
                                     "cooling": "cooling"}.get(power, self.state.system)
            self.state.lamp_hours = await self.projector.get_lamp_hours()
        except DeviceError:
            self.state.projector_power = "unknown"
        try:
            if self.state.system == "on":
                self.state.source = await self.switcher.get_route(1)
            else:
                await self.switcher.get_route(1)  # still poll so we know it's online
        except DeviceError:
            pass
        try:
            self.state.volume = await self.dsp.get_volume()
            self.state.muted = await self.dsp.get_mute()
        except DeviceError:
            pass
        before = dict(self.state.online)
        self._refresh_online()
        for dev, up in self.state.online.items():
            if before.get(dev) != up:
                self._event("info" if up else "error", f"{dev} {'online' if up else 'OFFLINE'}")

    async def poll_forever(self) -> None:
        while True:
            await self.poll_once()
            await asyncio.sleep(self.poll_interval)

    # --- views -----------------------------------------------------------
    def snapshot(self) -> dict:
        s = self.state
        return {
            "room_name": s.room_name,
            "system": s.system,
            "projector_power": s.projector_power,
            "source": s.source,
            "source_name": self.sources[s.source].name if s.source in self.sources else None,
            "sources": [{"input": i, "name": src.name} for i, src in sorted(self.sources.items())],
            "volume": s.volume,
            "muted": s.muted,
            "lamp_hours": s.lamp_hours,
            "status": s.status,
            "busy": s.busy,
            "online": s.online,
            "events": list(self.events),
            "joins": self.joins(),
        }

    def joins(self) -> dict:
        """The same state expressed as Crestron digital/analog/serial joins."""
        s = self.state
        digital = {
            J.DIG_SYSTEM_ON: s.system == "on",
            J.DIG_SYSTEM_OFF: s.system == "off",
            J.DIG_WARMING: s.system == "warming",
            J.DIG_COOLING: s.system == "cooling",
            J.DIG_MUTE_TOGGLE: s.muted,
            J.DIG_PROJ_ONLINE: s.online["projector"],
            J.DIG_SWITCHER_ONLINE: s.online["switcher"],
            J.DIG_DSP_ONLINE: s.online["dsp"],
        }
        for i in self.sources:
            digital[J.DIG_SOURCE_BASE + i] = s.source == i and s.system == "on"
        return {
            "digital": digital,
            "analog": {J.ANA_VOLUME: J.percent_to_analog(s.volume),
                       J.ANA_LAMP_HOURS: s.lamp_hours or 0},
            "serial": {J.SER_ROOM_NAME: s.room_name, J.SER_STATUS: s.status,
                       J.SER_SOURCE_NAME: self.sources[s.source].name if s.source in self.sources else ""},
        }
