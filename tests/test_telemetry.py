"""ABI boundary and reader lifecycle tests; no game or Windows installation needed."""
import ctypes
import math
import struct
import unittest

from engineer.telemetry import ACReader, DemoSource, Graphics, Physics, Static


class Clock:
    def __init__(self, value=1000.0):
        self.value = value

    def __call__(self):
        return self.value


def fixture():
    # Golden byte offsets deliberately independent of ctypes field construction.
    physics = bytearray(580)
    graphics = bytearray(296)
    static = bytearray(684)

    def text(buffer, offset, value):
        data = (value + "\0").encode("utf-16-le")
        buffer[offset:offset + len(data)] = data

    text(static, 0, "1.7")
    text(static, 30, "1.16.4")
    text(static, 68, "ks_ferrari_488_gt3")
    text(static, 134, "spa")
    text(static, 200, "Jos\N{LATIN SMALL LETTER E WITH ACUTE}")
    text(static, 266, "Driver")
    text(static, 524, "gp")
    struct.pack_into("<i", static, 412, 9000)  # maxRpm
    struct.pack_into("<f", static, 416, 100.0)  # maxFuel
    struct.pack_into("<f", static, 520, 7004.0)  # track length
    struct.pack_into("<i", physics, 0, 123)
    struct.pack_into("<fff", physics, 4, 0.8, 0.2, 42.5)
    struct.pack_into("<ii", physics, 16, 4, 6500)
    struct.pack_into("<ff", physics, 24, -0.23, 185.5)
    struct.pack_into("<4f", physics, 88, 26.0, 26.1, 26.2, 26.3)
    struct.pack_into("<4f", physics, 120, 99.1, 98.2, 97.3, 96.4)
    struct.pack_into("<4f", physics, 152, 80.0, 81.0, 82.0, 83.0)
    struct.pack_into("<ff", physics, 284, 24.0, 32.0)
    struct.pack_into("<f", physics, 564, 0.63)
    struct.pack_into("<iii", graphics, 0, 55, 2, 0)
    struct.pack_into("<i", graphics, 132, 3)
    struct.pack_into("<i", graphics, 140, 14500)
    struct.pack_into("<i", graphics, 144, 129000)
    text(graphics, 176, "Slick medium")
    struct.pack_into("<f", graphics, 248, 0.25)
    struct.pack_into("<f", graphics, 280, 0.98)
    return {"physics": physics, "graphics": graphics, "static": static}


class Mapping:
    def __init__(self, data):
        self.data, self.closed = data, False

    def read(self, start=0, size=None):
        if self.closed:
            raise OSError("Closed")
        end = len(self.data) if size is None else start + size
        return bytes(self.data[start:end])

    def close(self):
        self.closed = True


class TelemetryTests(unittest.TestCase):
    def reader(self, data=None):
        pages = data or fixture()
        clock = Clock()
        maps = {}

        def factory(name, size):
            key = name.rsplit("_", 1)[1]
            self.assertEqual(len(pages[key]), size)
            maps[key] = Mapping(pages[key])
            return maps[key]

        reader = ACReader(factory, clock)
        return reader, pages, clock, maps

    def test_windows_utf16_four_byte_packed_abi(self):
        self.assertEqual(ctypes.sizeof(Physics), 580)
        self.assertEqual(ctypes.sizeof(Graphics), 296)
        self.assertEqual(ctypes.sizeof(Static), 684)
        self.assertEqual(Physics.localVelocity.offset, 568)
        self.assertEqual(Graphics.normalizedCarPosition.offset, 248)
        self.assertEqual(Static.trackConfiguration.offset, 524)

    def test_decodes_golden_bytes_in_correct_units_without_claiming_wear_percent(self):
        reader, _, _, _ = self.reader()
        frame = reader.read()
        self.assertEqual(frame["driver"], "Jos\N{LATIN SMALL LETTER E WITH ACUTE} Driver")
        self.assertEqual(frame["car_id"], "ks_ferrari_488_gt3")
        self.assertEqual(frame["fuel_l"], 42.5)
        self.assertEqual(frame["gear"], "3")
        self.assertEqual(frame["tyre_core_c"], [80., 81., 82., 83.])
        self.assertAlmostEqual(frame["tyre_pressure_psi"][1], 26.1, places=4)
        self.assertAlmostEqual(frame["tyre_wear_raw"][0], 99.1, places=3)
        self.assertFalse(frame["capabilities"]["tyre_wear_calibrated"])
        self.assertIsNone(frame["lap_valid"])
        self.assertIsNone(frame["kers_charge"])
        self.assertFalse(frame["capabilities"]["energy"])

    def test_rejects_acc_evo_and_unknown_future_abis(self):
        for game_version, sm_version in [("1.9.8", "1.8"), ("0.6.1", "1.7"), ("1.16.4", "1.8"), ("1.17", "1.7")]:
            pages = fixture()
            pages["static"][:60] = bytearray(60)
            for offset, value in [(0, sm_version), (30, game_version)]:
                encoded = (value + "\0").encode("utf-16-le")
                pages["static"][offset:offset + len(encoded)] = encoded
            reader, _, _, mappings = self.reader(pages)
            self.assertIsNone(reader.read(), (game_version, sm_version))
            self.assertTrue(reader.last_error)
            self.assertTrue(all(mapping.closed for mapping in mappings.values()))

    def test_statuses_do_not_silently_become_live(self):
        reader, pages, clock, _ = self.reader()
        for value, status in [(0, "menu"), (1, "replay"), (3, "paused"), (2, "live")]:
            struct.pack_into("<i", pages["graphics"], 4, value)
            struct.pack_into("<i", pages["graphics"], 0, value + 10)
            self.assertEqual(reader.read()["status"], status)
            clock.value += 1

    def test_missing_game_never_creates_maps_and_retries_without_throwing(self):
        opened = []
        clock = Clock()

        def missing(name, size):
            opened.append(name)
            raise OSError("not found")

        reader = ACReader(missing, clock)
        self.assertIsNone(reader.read())
        self.assertIsNone(reader.read())
        self.assertEqual(len(opened), 1)
        clock.value += 1.1
        self.assertIsNone(reader.read())
        self.assertEqual(len(opened), 2)
        self.assertFalse(reader.connected)
        self.assertFalse(reader.transient)

    def test_stale_live_feed_disconnects_while_paused_feed_remains_paused(self):
        reader, pages, clock, mappings = self.reader()
        self.assertIsNotNone(reader.read())
        self.assertTrue(reader.connected)
        self.assertFalse(reader.transient)
        clock.value += 3.1
        self.assertIsNone(reader.read())
        self.assertFalse(reader.connected)
        self.assertFalse(reader.transient)
        self.assertTrue(all(mapping.closed for mapping in mappings.values()))
        reader2, pages2, clock2, _ = self.reader()
        struct.pack_into("<i", pages2["graphics"], 4, 3)
        self.assertEqual(reader2.read()["status"], "paused")
        clock2.value += 60
        self.assertEqual(reader2.read()["status"], "paused")

    def test_torn_packet_snapshot_is_never_accepted(self):
        class Moving(Mapping):
            def read(self, start=0, size=None):
                number = struct.unpack_from("<i", self.data)[0]
                struct.pack_into("<i", self.data, 0, number + 1)
                return super().read(start, size)

        pages, clock = fixture(), Clock()

        def factory(name, size):
            key = name.rsplit("_", 1)[1]
            return (Moving if key == "physics" else Mapping)(pages[key])

        reader = ACReader(factory, clock)
        self.assertIsNone(reader.read())
        self.assertIn("coherent", reader.last_error)
        self.assertTrue(reader.transient)
        self.assertTrue(reader.connected)
        reader.close()
        self.assertFalse(reader.transient)
        self.assertFalse(reader.connected)

    def test_transient_resets_on_next_valid_read_and_other_failure_paths(self):
        class Switchable(Mapping):
            moving = True

            def read(self, start=0, size=None):
                if self.moving:
                    packet = struct.unpack_from("<i", self.data)[0]
                    struct.pack_into("<i", self.data, 0, packet + 1)
                return super().read(start, size)

        pages, clock, maps = fixture(), Clock(), {}
        alive = [True]

        def factory(name, size):
            key = name.rsplit("_", 1)[1]
            mapping = (Switchable if key == "physics" else Mapping)(pages[key])
            maps[key] = mapping
            return mapping

        reader = ACReader(factory, clock, process_probe=lambda: alive[0])
        self.assertIsNone(reader.read())
        self.assertTrue(reader.transient)
        maps["physics"].moving = False
        self.assertIsNotNone(reader.read())
        self.assertFalse(reader.transient)
        self.assertTrue(reader.connected)
        maps["physics"].moving = True
        self.assertIsNone(reader.read())
        self.assertTrue(reader.transient)
        alive[0] = False
        clock.value += 1.1
        self.assertIsNone(reader.read())
        self.assertFalse(reader.transient)
        self.assertFalse(reader.connected)

    def test_game_exit_from_pause_disconnects_even_when_mappings_survive(self):
        pages, clock = fixture(), Clock()
        struct.pack_into("<i", pages["graphics"], 4, 3)
        alive = [True]
        mappings = []

        def factory(name, size):
            mapping = Mapping(pages[name.rsplit("_", 1)[1]])
            mappings.append(mapping)
            return mapping

        reader = ACReader(factory, clock, process_probe=lambda: alive[0])
        self.assertEqual(reader.read()["status"], "paused")
        alive[0] = False
        clock.value += 1.1
        self.assertIsNone(reader.read())
        self.assertTrue(all(mapping.closed for mapping in mappings))

    def test_energy_requires_supported_car_valid_capacity_and_charge(self):
        reader, pages, _, _ = self.reader()
        struct.pack_into("<i", pages["static"], 500, 1)  # hasERS
        struct.pack_into("<i", pages["static"], 516, 5)  # controller count
        struct.pack_into("<f", pages["static"], 592, 4000000.)  # ersMaxJ
        struct.pack_into("<f", pages["physics"], 256, 0.73)
        struct.pack_into("<ii", pages["physics"], 320, 4, 2)
        frame = reader.read()
        self.assertTrue(frame["capabilities"]["energy"])
        self.assertAlmostEqual(frame["kers_charge"], 0.73, places=4)
        self.assertEqual(frame["ers_power_level"], 2)
        self.assertEqual(frame["ers_recovery_level"], 4)
        struct.pack_into("<f", pages["physics"], 256, float("nan"))
        frame = reader.read()
        self.assertIsNone(frame["kers_charge"])
        self.assertIsNone(frame["ers_power_level"])
        self.assertFalse(frame["capabilities"]["energy"])

    def test_nan_channels_are_null_and_nan_position_rejects_frame(self):
        reader, pages, _, _ = self.reader()
        struct.pack_into("<f", pages["physics"], 88, float("nan"))
        self.assertIsNone(reader.read()["tyre_pressure_psi"][0])
        struct.pack_into("<f", pages["graphics"], 248, float("nan"))
        self.assertIsNone(reader.read())


class DemoTests(unittest.TestCase):
    def test_demo_starts_midlap_and_is_poll_rate_independent(self):
        clock = Clock()
        first = DemoSource(clock)
        second = DemoSource(clock)
        frame = first.read()
        self.assertEqual(frame["source"], "demo")
        self.assertTrue(0.1 < frame["normalized_pos"] < 0.9)
        for i in range(10):
            clock.value += 0.1
            first.read()
        self.assertEqual(first.read(), second.read())
        self.assertIsNone(frame["kers_charge"])

    def test_demo_lap_crossings_correct_timing_fuel_and_braking_variation(self):
        clock = Clock()
        demo = DemoSource(clock)
        previous = demo.read()
        crossings, braking, steering = [], [], []
        for step in range(1, 3601):
            clock.value = 1000 + step * 0.05
            frame = demo.read()
            if frame["lap_count"] != previous["lap_count"]:
                self.assertLess(frame["normalized_pos"], 0.003)
                crossings.append(frame["last_lap_ms"])
            self.assertLessEqual(frame["fuel_l"], previous["fuel_l"])
            self.assertTrue(math.isfinite(frame["speed_kmh"]))
            braking.append(frame["brake"])
            steering.append(frame["steer"])
            previous = frame
        self.assertGreaterEqual(len(crossings), 5)
        self.assertIn(32000, crossings)
        self.assertIn(35000, crossings)
        self.assertGreater(max(braking), 0.8)
        self.assertLess(min(steering), -0.25)
        self.assertGreater(max(steering), 0.25)


if __name__ == "__main__":
    unittest.main()
