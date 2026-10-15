"""Run with:  python -m unittest -v"""

import unittest

from avsim.devices.dsp import AudioDSP
from avsim.devices.projector import PJLinkProjector
from avsim.devices.switcher import MatrixSwitcher
from avsim.drivers.ascii_devices import DSPDriver, SwitcherDriver
from avsim.drivers.base import DeviceError, DeviceOffline
from avsim.drivers.pjlink import PJLinkDriver, PJLinkError
from avsim.room import Room, Source


class SimTestCase(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.proj = PJLinkProjector(warmup=0.2, cooldown=0.2)
        self.sw = MatrixSwitcher()
        self.dsp = AudioDSP()
        for sim in (self.proj, self.sw, self.dsp):
            await sim.start()  # port 0 -> OS picks a free port
        self.pj = PJLinkDriver("127.0.0.1", self.proj.port, timeout=0.5)
        self.swd = SwitcherDriver("127.0.0.1", self.sw.port, timeout=0.5)
        self.dspd = DSPDriver("127.0.0.1", self.dsp.port, timeout=0.5)

    async def asyncTearDown(self):
        for d in (self.pj, self.swd, self.dspd):
            await d.close()
        for sim in (self.proj, self.sw, self.dsp):
            await sim.stop()

    def make_room(self):
        return Room(self.pj, self.swd, self.dspd,
                    [Source(1, "Laptop"), Source(2, "PC"), Source(3, "Wireless")],
                    warmup_timeout=3, poll_interval=0.1)


class TestPJLink(SimTestCase):
    async def test_power_cycle(self):
        self.assertEqual(await self.pj.get_power(), "off")
        await self.pj.power_on()
        self.assertEqual(await self.pj.get_power(), "warming")
        await __import__("asyncio").sleep(0.3)
        self.assertEqual(await self.pj.get_power(), "on")

    async def test_input_change_while_off_is_err3(self):
        with self.assertRaises(PJLinkError) as ctx:
            await self.pj.set_input("hdmi1")
        self.assertEqual(ctx.exception.code, "ERR3")

    async def test_authentication(self):
        await self.proj.stop()
        self.proj = PJLinkProjector(password="secret")
        await self.proj.start()
        good = PJLinkDriver("127.0.0.1", self.proj.port, password="secret", timeout=0.5)
        self.assertEqual(await good.get_name(), "Room Projector")
        await good.close()
        bad = PJLinkDriver("127.0.0.1", self.proj.port, password="wrong", timeout=0.5)
        with self.assertRaises(DeviceError):
            await bad.get_name()
        await bad.close()


class TestAsciiDevices(SimTestCase):
    async def test_route_and_query(self):
        await self.swd.route(3, 1)
        self.assertEqual(await self.swd.get_route(1), 3)

    async def test_bad_route_raises(self):
        with self.assertRaises(DeviceError):
            await self.swd.route(9, 1)

    async def test_volume_is_clamped(self):
        self.assertEqual(await self.dspd.set_volume(150), 100)
        self.assertTrue(await self.dspd.set_mute(True))

    async def test_offline_detection(self):
        await self.dsp.stop()
        await self.dspd.close()
        with self.assertRaises(DeviceOffline):
            await self.dspd.get_volume()
        self.assertFalse(self.dspd.online)


class TestRoomMacros(SimTestCase):
    async def test_system_on_and_off(self):
        room = self.make_room()
        await room.system_on(source=2)
        self.assertEqual(room.state.system, "on")
        self.assertEqual(self.proj.power, "1")
        self.assertEqual(self.proj.input, "31")
        self.assertEqual(self.sw.routes, {1: 2, 2: 2})
        self.assertEqual((self.dsp.volume, self.dsp.mute), (40, False))

        await room.system_off()
        self.assertEqual(room.state.system, "off")
        self.assertEqual(self.proj.power, "0")
        self.assertTrue(self.dsp.mute)

    async def test_source_press_turns_room_on(self):
        room = self.make_room()
        await room.select_source(3)
        self.assertEqual(room.state.system, "on")
        self.assertEqual(room.state.source, 3)

    async def test_macro_survives_dead_dsp(self):
        await self.dsp.stop()
        room = self.make_room()
        await room.system_on()
        self.assertEqual(self.proj.power, "1")       # projector still came up
        self.assertEqual(self.sw.routes[1], 1)
        self.assertTrue(any(e["level"] == "error" for e in room.events))

    async def test_join_feedback(self):
        room = self.make_room()
        await room.system_on(source=1)
        j = room.joins()
        self.assertTrue(j["digital"][1])    # system on
        self.assertTrue(j["digital"][11])   # source 1 active
        self.assertEqual(j["analog"][1], round(40 * 65535 / 100))


if __name__ == "__main__":
    unittest.main()
