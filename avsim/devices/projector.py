"""Simulated projector speaking PJLink Class 1.

PJLink is the open standard (from JBMIA) that most projector brands
support on TCP port 4352. This simulator implements the commands a room
controller actually uses:

    %1POWR 1 / 0 / ?   power on / off / query (0=off 1=on 2=cooling 3=warm-up)
    %1INPT 31 / ?      select input / query (1x=RGB, 3x=digital/HDMI)
    %1AVMT 31 / 30 / ? A/V mute on / off / query
    %1LAMP ?           lamp hours
    %1NAME ?           projector name
    %1ERST ?           error status

Errors follow the spec: ERR1 undefined command, ERR2 out of parameter,
ERR3 unavailable time (e.g. changing input while warming up), ERR4 failure.

Optional PJLink authentication: if a password is set, the greeting is
"PJLINK 1 <seed>" and the first command must be prefixed with
md5(seed + password). A bad digest gets "PJLINK ERRA" and the socket closes.
"""

import asyncio
import hashlib
import secrets
import time

from .base import LineDevice

OFF, ON, COOLING, WARMING = "0", "1", "2", "3"


class PJLinkProjector(LineDevice):
    name = "projector"
    reply_eol = "\r"  # PJLink replies end in CR only

    VALID_INPUTS = {"11", "12", "31", "32", "33"}

    def __init__(self, host="127.0.0.1", port=0, *, warmup=8.0, cooldown=8.0,
                 password: str | None = None, projector_name="Room Projector"):
        super().__init__(host, port)
        self.warmup = warmup
        self.cooldown = cooldown
        self.password = password
        self.projector_name = projector_name
        self.power = OFF
        self.input = "31"
        self.av_mute = "30"
        self._lamp_seconds = 1234 * 3600.0
        self._on_since: float | None = None
        self._transition: asyncio.TimerHandle | None = None

    # --- state machine ---------------------------------------------------
    def _set_power(self, value: str) -> None:
        if value == ON:
            self._on_since = time.monotonic()
        elif self._on_since is not None:
            self._lamp_seconds += time.monotonic() - self._on_since
            self._on_since = None
        self.power = value
        self.log.info("power -> %s", {OFF: "off", ON: "on", COOLING: "cooling", WARMING: "warming"}[value])

    def _schedule(self, delay: float, value: str) -> None:
        if self._transition:
            self._transition.cancel()
        loop = asyncio.get_running_loop()
        self._transition = loop.call_later(delay, self._set_power, value)

    def lamp_hours(self) -> int:
        total = self._lamp_seconds
        if self._on_since is not None:
            total += time.monotonic() - self._on_since
        return int(total // 3600)

    # --- protocol --------------------------------------------------------
    def greeting(self, session):
        if self.password:
            session["seed"] = secrets.token_hex(4)
            session["authed"] = False
            return f"PJLINK 1 {session['seed']}"
        session["authed"] = True
        return "PJLINK 0"

    def handle_line(self, line, session):
        if not session.get("authed"):
            expected = hashlib.md5((session["seed"] + self.password).encode()).hexdigest()
            if not line.startswith(expected):
                session["close"] = True
                return "PJLINK ERRA"
            session["authed"] = True
            line = line[len(expected):]

        if not line.startswith("%1") or len(line) < 7:
            return "%1????=ERR1"

        cmd, _, param = line[2:].partition(" ")
        cmd = cmd.upper()
        handler = getattr(self, f"_cmd_{cmd.lower()}", None)
        if handler is None:
            return f"%1{cmd}=ERR1"
        return f"%1{cmd}={handler(param.strip())}"

    def _cmd_powr(self, p):
        if p == "?":
            return self.power
        if p == "1":
            if self.power == COOLING:
                return "ERR3"
            if self.power == OFF:
                self._set_power(WARMING)
                self._schedule(self.warmup, ON)
            return "OK"
        if p == "0":
            if self.power == WARMING:
                return "ERR3"
            if self.power == ON:
                self._set_power(COOLING)
                self._schedule(self.cooldown, OFF)
            return "OK"
        return "ERR2"

    def _cmd_inpt(self, p):
        if p == "?":
            return self.input if self.power == ON else "ERR3"
        if p not in self.VALID_INPUTS:
            return "ERR2"
        if self.power != ON:
            return "ERR3"
        self.input = p
        self.log.info("input -> %s", p)
        return "OK"

    def _cmd_avmt(self, p):
        if p == "?":
            return self.av_mute
        if p not in {"10", "11", "20", "21", "30", "31"}:
            return "ERR2"
        if self.power != ON:
            return "ERR3"
        self.av_mute = p
        return "OK"

    def _cmd_lamp(self, p):
        if p != "?":
            return "ERR2"
        return f"{self.lamp_hours()} {1 if self.power == ON else 0}"

    def _cmd_name(self, p):
        return self.projector_name if p == "?" else "ERR2"

    def _cmd_erst(self, p):
        # fan, lamp, temp, cover, filter, other: 0 = OK
        return "000000" if p == "?" else "ERR2"
