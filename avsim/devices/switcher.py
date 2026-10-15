"""Simulated 4x2 HDMI matrix switcher with a simple ASCII protocol.

Protocol (commands end in CR and/or LF, replies end in CR+LF):

    ROUTE <in> <out>   route input 1-4 to output 1-2  -> ROUTE <in> <out> OK
    ROUTE? <out>       which input is on an output     -> ROUTE <in> <out>
    SIGNAL? <in>       is there a live signal on input -> SIGNAL <in> 0|1
    VER?               firmware version                -> VER 1.4.2
    anything else                                       -> ERR <reason>

Real switchers (Crestron DM / HD-MD, Extron, Kramer, AVPro...) each have
their own command set, but they all look roughly like this.
"""

from .base import LineDevice


class MatrixSwitcher(LineDevice):
    name = "switcher"

    def __init__(self, host="127.0.0.1", port=0, *, inputs=4, outputs=2):
        super().__init__(host, port)
        self.inputs = inputs
        self.outputs = outputs
        self.routes = {out: 1 for out in range(1, outputs + 1)}
        # Pretend the laptop and room PC are plugged in and live.
        self.signal = {i: i in (1, 2) for i in range(1, inputs + 1)}

    def _int(self, text, low, high):
        try:
            value = int(text)
        except (TypeError, ValueError):
            return None
        return value if low <= value <= high else None

    def handle_line(self, line, session):
        parts = line.upper().split()
        cmd, args = parts[0], parts[1:]

        if cmd == "ROUTE" and len(args) == 2:
            src = self._int(args[0], 1, self.inputs)
            dst = self._int(args[1], 1, self.outputs)
            if src is None or dst is None:
                return "ERR RANGE"
            self.routes[dst] = src
            self.log.info("input %s -> output %s", src, dst)
            return f"ROUTE {src} {dst} OK"

        if cmd == "ROUTE?" and len(args) == 1:
            dst = self._int(args[0], 1, self.outputs)
            if dst is None:
                return "ERR RANGE"
            return f"ROUTE {self.routes[dst]} {dst}"

        if cmd == "SIGNAL?" and len(args) == 1:
            src = self._int(args[0], 1, self.inputs)
            if src is None:
                return "ERR RANGE"
            return f"SIGNAL {src} {int(self.signal[src])}"

        if cmd == "VER?":
            return "VER 1.4.2"

        return "ERR SYNTAX"
