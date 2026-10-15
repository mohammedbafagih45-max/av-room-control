"""Simulated audio DSP (think Biamp Tesira / QSC Q-SYS / Crestron DMPS audio).

Protocol (commands end in CR and/or LF, replies end in CR+LF):

    SET VOL <0-100>   -> VOL <n>
    GET VOL           -> VOL <n>
    SET MUTE <0|1>    -> MUTE <0|1>
    GET MUTE          -> MUTE <0|1>
    anything else     -> ERR <reason>
"""

from .base import LineDevice


class AudioDSP(LineDevice):
    name = "dsp"

    def __init__(self, host="127.0.0.1", port=0):
        super().__init__(host, port)
        self.volume = 50
        self.mute = False

    def handle_line(self, line, session):
        parts = line.upper().split()
        if len(parts) < 2 or parts[0] not in ("SET", "GET"):
            return "ERR SYNTAX"
        verb, block = parts[0], parts[1]

        if block == "VOL":
            if verb == "SET":
                if len(parts) != 3 or not parts[2].isdigit() or not 0 <= int(parts[2]) <= 100:
                    return "ERR RANGE"
                self.volume = int(parts[2])
                self.log.info("volume -> %s", self.volume)
            return f"VOL {self.volume}"

        if block == "MUTE":
            if verb == "SET":
                if len(parts) != 3 or parts[2] not in ("0", "1"):
                    return "ERR RANGE"
                self.mute = parts[2] == "1"
                self.log.info("mute -> %s", self.mute)
            return f"MUTE {int(self.mute)}"

        return "ERR BLOCK"
