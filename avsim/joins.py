"""Crestron-style join map.

On a Crestron system, the touch panel and the program talk through numbered
"joins": digital (true/false, buttons and LEDs), analog (0-65535, sliders and
gauges) and serial (text). The same numbers are used here so the web panel,
the Python controller and the SIMPL# reference program in /crestron all agree.

See the join map table in README.md.
"""

# Digital joins: press = panel -> program, feedback = program -> panel
DIG_SYSTEM_ON = 1
DIG_SYSTEM_OFF = 2
DIG_WARMING = 3          # feedback only
DIG_COOLING = 4          # feedback only
DIG_VOL_UP = 5
DIG_VOL_DOWN = 6
DIG_MUTE_TOGGLE = 7
DIG_PROJ_ONLINE = 21     # feedback only
DIG_SWITCHER_ONLINE = 22  # feedback only
DIG_DSP_ONLINE = 23      # feedback only
DIG_SOURCE_BASE = 10     # source n = join 10 + n  (11-14)

# Analog joins (Crestron analogs are 0-65535)
ANA_VOLUME = 1
ANA_LAMP_HOURS = 2

# Serial joins
SER_ROOM_NAME = 1
SER_STATUS = 2
SER_SOURCE_NAME = 3


def percent_to_analog(percent: int) -> int:
    """Scale 0-100 % to a Crestron 0-65535 analog value."""
    return round(max(0, min(100, percent)) * 65535 / 100)


def analog_to_percent(value: int) -> int:
    return round(max(0, min(65535, value)) * 100 / 65535)
