#!/usr/bin/env python3
"""Start the room: simulated devices, the room controller and the web panel.

    python run.py                 # everything simulated, panel on :8080
    python run.py --no-sim        # control the real devices listed in config.json
    python run.py --web-port 9000 --verbose
"""

import argparse
import asyncio
import json
import logging
from pathlib import Path

from avsim.devices.dsp import AudioDSP
from avsim.devices.projector import PJLinkProjector
from avsim.devices.switcher import MatrixSwitcher
from avsim.drivers.ascii_devices import DSPDriver, SwitcherDriver
from avsim.drivers.pjlink import PJLinkDriver
from avsim.room import Room, Source
from avsim.web_server import start_web_server


async def main(args) -> None:
    cfg = json.loads(Path(args.config).read_text())
    dev = cfg["devices"]

    sims = []
    if not args.no_sim:
        sim_cfg = cfg.get("simulator", {})
        sims = [
            PJLinkProjector(dev["projector"]["host"], dev["projector"]["port"],
                            warmup=sim_cfg.get("projector_warmup_seconds", 6),
                            cooldown=sim_cfg.get("projector_cooldown_seconds", 6),
                            password=dev["projector"].get("password")),
            MatrixSwitcher(dev["switcher"]["host"], dev["switcher"]["port"]),
            AudioDSP(dev["dsp"]["host"], dev["dsp"]["port"]),
        ]
        for sim in sims:
            await sim.start()

    room = Room(
        PJLinkDriver(dev["projector"]["host"], dev["projector"]["port"],
                     password=dev["projector"].get("password")),
        SwitcherDriver(dev["switcher"]["host"], dev["switcher"]["port"]),
        DSPDriver(dev["dsp"]["host"], dev["dsp"]["port"]),
        [Source(**s) for s in cfg["sources"]],
        room_name=cfg.get("room_name", "Conference Room"),
        default_source=cfg.get("default_source", 1),
        default_volume=cfg.get("default_volume", 40),
        warmup_timeout=cfg.get("warmup_timeout", 60),
        poll_interval=cfg.get("poll_interval", 2.0),
    )

    web = start_web_server(room, asyncio.get_running_loop(), args.web_host, args.web_port)
    print(f"\n  Touch panel:  http://{args.web_host}:{web.server_address[1]}")
    print("  Press Ctrl+C to stop.\n")
    try:
        await room.poll_forever()
    finally:
        web.shutdown()
        for sim in sims:
            await sim.stop()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--config", default="config.json")
    parser.add_argument("--no-sim", action="store_true", help="don't start simulators; use real devices")
    parser.add_argument("--web-host", default="127.0.0.1")
    parser.add_argument("--web-port", type=int, default=8080)
    parser.add_argument("-v", "--verbose", action="store_true", help="log every command sent and received")
    args = parser.parse_args()

    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                        format="%(asctime)s %(name)-18s %(message)s", datefmt="%H:%M:%S")
    try:
        asyncio.run(main(args))
    except KeyboardInterrupt:
        print("\nStopped.")
