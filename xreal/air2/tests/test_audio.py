"""Offline Air 2 audio regression tests. See ../docs/motorola-audio.md for setup."""

import struct
import unittest
from pathlib import Path

from unicorn import UC_ARCH_ARM, UC_HOOK_CODE, UC_MODE_THUMB, Uc
from unicorn.arm_const import (
    UC_ARM_REG_LR, UC_ARM_REG_PC, UC_ARM_REG_R0, UC_ARM_REG_R1, UC_ARM_REG_R2,
    UC_ARM_REG_R4, UC_ARM_REG_SP,
)

from xreal.air2 import build_beam_cover_handoff, build_dp, build_mcu


FIRMWARE = Path(__file__).resolve().parents[3] / "firmware" / "air2"
SESSION = 0x20000248
ADDRESS = 0x20000330
BRIDGE_STATE = 0x2000037A
BRIGHTNESS_PLUS = 0x20000D88
BRIGHTNESS_MINUS = 0x20000DA0
USB_CONTROL = 0x400C0010
USB_ENABLED = 0x2000025B
END = 0x24000


class AudioTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.stock = (FIRMWARE / "09.1.00.180_20240507.bin").read_bytes()
        build_mcu.verify_stock(cls.stock)
        cls.image = build_mcu.build(cls.stock)
        build_mcu.verify_output(cls.stock, cls.image)
        cls.dp_first = build_mcu.build_dp_first(cls.image)
        cls.immediate = build_mcu.build_dp_first(cls.image, immediate=True)

        timed_trace = build_mcu.build_timed_audio_trace(cls.immediate)
        bridge_trace = build_mcu.build_bridge_audio_trace(timed_trace)
        cls.audio_route_buttons_v2 = build_mcu.build_audio_route_buttons(bridge_trace)
        cls.beam_mcu, cls.beam_dp = build_beam_cover_handoff.build_pair(
            cls.stock, (FIRMWARE / "1140").read_bytes())

    def setUp(self):
        # Addresses in the trace are container offsets. All executed calls are PC-relative.
        self.cpu = Uc(UC_ARCH_ARM, UC_MODE_THUMB)
        for addr, size in ((0, 0x25000), (0x20000000, 0x20000),
                           (0x40000000, 0x1000), (0x400C0000, 0x1000)):
            self.cpu.mem_map(addr, size)
        self.cpu.mem_write(0, self.image)
        self.cpu.reg_write(UC_ARM_REG_SP, 0x2001FFF0)
        self.calls = []
        self.status = 0xA1  # E08B eligible initial wait, no Adapter Attention
        self.stubs = {0x5210: lambda: self.status,
                      0x47A6: lambda: self.byte(BRIDGE_STATE + 2)}
        self.cpu.hook_add(UC_HOOK_CODE, self.on_instruction)

    def byte(self, addr):
        return self.cpu.mem_read(addr, 1)[0]

    def on_instruction(self, cpu, addr, size, _):
        if addr in self.stubs:
            self.calls.append((addr, cpu.reg_read(UC_ARM_REG_R0)))
            cpu.reg_write(UC_ARM_REG_R0, self.stubs[addr]())
            cpu.reg_write(UC_ARM_REG_PC, cpu.reg_read(UC_ARM_REG_LR))

    def run_code(self, start, stop=END):
        self.cpu.reg_write(UC_ARM_REG_LR, END | 1)
        self.cpu.emu_start(start | 1, stop, count=500)
        self.assertEqual(self.cpu.reg_read(UC_ARM_REG_PC), stop)

    def test_addressed_host_and_cold_reconnect(self):
        self.cpu.mem_write(ADDRESS, struct.pack("<I", 7))
        self.run_code(0x3140)
        self.assertEqual(self.byte(SESSION), 0xA9)
        self.cpu.mem_write(ADDRESS, bytes(4))
        self.run_code(0x3140)
        self.assertEqual(self.byte(SESSION), 0xA9)  # latched for this power session

        # Start with DP selected, execute the real cold-init defaults up to the next setter.
        self.cpu.mem_write(BRIDGE_STATE + 2, b"\x01")
        self.stubs.update(dict.fromkeys((0x5088, 0x592C, 0x59E0), lambda: 0))
        self.run_code(0x50EC, 0x50FE)
        self.assertEqual(self.byte(BRIDGE_STATE + 2), 0)

    def test_fallback_requires_continuous_eligible_wait(self):
        self.run_code(0x3140)
        self.assertEqual(self.byte(SESSION), 1)
        self.status = 0
        self.run_code(0x3140)
        self.assertEqual(self.byte(SESSION), 0)
        self.status = 0xA1
        for _ in range(165):
            self.run_code(0x3140)
        self.assertEqual(self.byte(SESSION), 165)
        self.run_code(0x3140, 0x3196)  # entry to full no-USB transition
        self.assertEqual(self.byte(SESSION), 166)

    def test_address_on_last_poll_wins(self):
        self.cpu.mem_write(SESSION, bytes([165]))
        self.cpu.mem_write(ADDRESS, struct.pack("<I", 1))
        self.run_code(0x3140)
        self.assertEqual(self.byte(SESSION), 0xA9)
        self.assertNotIn(0x47A6, [addr for addr, arg in self.calls])

    def test_no_usb_retry_restores_gain_and_locks_button(self):
        self.cpu.mem_write(SESSION, b"\xa6")
        self.cpu.mem_write(0x20000E52, struct.pack("<H", 0x180))
        for addr in (0x16A3A, 0x59E0, 0x112C8):
            self.stubs[addr] = lambda: 0
        self.run_code(0xFD00)
        self.assertEqual(self.byte(SESSION), 0xA7)
        self.assertEqual(self.byte(BRIDGE_STATE + 7), 1)
        self.assertEqual(self.byte(0x20000D92) & 8, 8)
        self.assertIn((0x112C8, 0x180), self.calls)
        self.assertEqual([arg for addr, arg in self.calls if addr == 0x16A3A], [2000, 1000])
        self.calls.clear()
        self.run_code(0xFD00)
        self.assertEqual(self.calls, [])

    def test_four_second_button_gate(self):
        self.cpu.mem_write(0x20000D8A, b"\x04")  # key event consumed by the dispatcher
        self.stubs[0x7C4C] = lambda: 4000 * 4  # 4 timer ticks/ms, press started at 0
        self.stubs[0x185E8] = lambda: 0
        self.run_code(0xACA6, 0xAD0A)
        self.assertIn(0x185E8, [addr for addr, arg in self.calls])
        self.calls.clear()
        self.cpu.mem_write(0x20000D92, b"\x08")  # production no-USB session lock
        self.stubs[0x187C8] = lambda: 0  # shorter press has a different action
        self.run_code(0xACA6, 0xAD0A)
        self.assertNotIn(0x185E8, [addr for addr, arg in self.calls])

    def stub_toggle_io(self):
        # Exercise the stock toggle and E0B8/B6 setters. Stub bus I/O, logging,
        # timing, amplifier and peripheral subroutines at their call boundaries.
        for addr in (0x9568, 0xB090, 0x59E0, 0x9EB0, 0x11410, 0x5A4C, 0x1E04,
                     0x5A70, 0x7C4C, 0x1EA4, 0x8618, 0x12648):
            self.stubs[addr] = lambda: 0

    def test_manual_switch_in_both_directions(self):
        self.stub_toggle_io()
        self.cpu.mem_write(USB_CONTROL, struct.pack("<I", 0x90))
        self.cpu.mem_write(USB_ENABLED, b"\x01")
        self.cpu.mem_write(ADDRESS, struct.pack("<I", 7))  # even on an enumerated host
        self.run_code(0x185E8)
        self.assertEqual(self.byte(BRIDGE_STATE + 2), 1)
        self.assertEqual(self.byte(BRIDGE_STATE + 7), 1)
        self.assertEqual(self.byte(USB_CONTROL) & 0x90, 0)
        self.assertEqual(self.byte(USB_ENABLED), 0)
        self.assertIn(0x1E04, [addr for addr, arg in self.calls])

        self.calls.clear()
        self.run_code(0x185E8)
        self.assertEqual(self.byte(BRIDGE_STATE + 2), 0)
        self.assertEqual(self.byte(BRIDGE_STATE + 7), 0)
        usb_init = [addr for addr, arg in self.calls if addr in (0x1EA4, 0x8618, 0x12648)]
        self.assertEqual(usb_init, [0x1EA4, 0x8618, 0x12648])

    def test_dp_to_usb_cover_handoff_holds_until_ack_or_timeout(self):
        image = self.beam_mcu
        self.cpu.mem_write(0, image)
        self.stub_toggle_io()
        ticks = 4000
        self.stubs[0x7C4C] = lambda: ticks
        self.stubs[0x1E264] = lambda: 0
        pending = 0x2001C50C
        self.stubs[0xB090] = lambda: self.assertEqual(self.byte(pending), 1) or 0
        self.cpu.mem_write(BRIDGE_STATE + 2, b"\x01")
        self.run_code(0x185E8)
        self.assertEqual(self.byte(BRIDGE_STATE + 2), 0)
        self.assertEqual(self.byte(BRIDGE_STATE + 7), 0)
        for entry in (0xB10C, 0x65FC):
            self.run_code(entry)  # Neither open function enters its stock body.
        self.assertEqual(self.byte(pending), 1)

        ticks += 11999
        self.run_code(0x1E430)
        self.assertEqual(self.byte(pending), 1)
        self.send_cover_ack()
        self.assertEqual(self.byte(pending), 2)
        self.stubs[0x65FC] = lambda: self.assertEqual(self.byte(pending), 0) or 0
        self.run_code(0x1E430)
        self.assertEqual(self.byte(pending), 0)
        self.assertEqual(self.calls[-1][0], 0x65FC)
        del self.stubs[0x65FC]
        for entry, continuation in ((0xB10C, 0xB110), (0x65FC, 0x6600)):
            self.run_code(entry, continuation)
            self.cpu.reg_write(UC_ARM_REG_SP, 0x2001FFF0)

        self.cpu.mem_write(BRIDGE_STATE + 2, b"\x01")
        self.run_code(0x185E8)
        self.run_code(0xB10C)
        self.stubs[0xB10C] = lambda: self.assertEqual(self.byte(pending), 0) or 0
        ticks += 12000
        self.run_code(0x1E430)
        self.assertEqual(self.byte(pending), 0)
        self.assertEqual(self.calls[-1][0], 0xB10C)

    def send_cover_ack(self):
        packet = 0x20001800
        length = 0x20001880
        self.cpu.mem_write(packet, b"\xa7\xad\x04")
        self.cpu.reg_write(UC_ARM_REG_R0, packet)
        self.cpu.reg_write(UC_ARM_REG_R1, length)
        self.cpu.reg_write(UC_ARM_REG_R4, 0x2B)
        self.run_code(0x1DFC0)
        self.assertEqual(self.byte(length), 5)
        self.assertEqual(bytes(self.cpu.mem_read(packet, 5)), b"BHA1\x01")

    def test_usb_to_dp_cover_handoff_waits_for_the_current_ack_or_timeout(self):
        image = self.beam_mcu
        self.setup_route_buttons(image)
        packet, length = 0x20001800, 0x20001880
        self.stubs[0x13B2C] = lambda: 0
        self.run_code(0x1161A, 0x1161E)
        self.assertEqual(self.cpu.mem_read(0x2001C500, 16), bytes(16))

        def exchange(page, token=0):
            self.cpu.mem_write(packet, struct.pack("<BBBI", 0xA7, 0xAD, page, token))
            self.cpu.reg_write(UC_ARM_REG_R0, packet)
            self.cpu.reg_write(UC_ARM_REG_R1, length)
            self.cpu.reg_write(UC_ARM_REG_R4, 0x2B)
            self.run_code(0x1DFC0)
            self.assertEqual(self.byte(length), 9)
            return struct.unpack("<4sBI", self.cpu.mem_read(packet, 9))

        for start, send_ack, host_selected_dp in ((1000, True, False), (10000, False, True)):
            self.cpu.mem_write(BRIDGE_STATE + 2, b"\x00")
            self.cpu.mem_write(USB_CONTROL, b"\x90")
            self.cpu.mem_write(USB_ENABLED, b"\x01")
            if host_selected_dp:
                self.cpu.reg_write(UC_ARM_REG_R0, 1)
                self.run_code(0x47C4)
            self.poll_buttons(start, 1, False)
            self.poll_buttons(start + 4000, 1, False)
            token = (start + 4000) * 4
            self.assertEqual(exchange(5), (b"BHD1", 1, token))
            self.assertEqual(self.byte(USB_ENABLED), 1)
            self.assertEqual(self.byte(BRIDGE_STATE + 2), int(host_selected_dp))
            self.assertEqual(exchange(6, token - 1), (b"BHD1", 0, token))
            if send_ack:
                self.assertEqual(exchange(6, token), (b"BHD1", 1, token))
            delay = 50 if send_ack else 3000
            self.poll_buttons(start + 4000 + delay - 1, 3, False)
            self.assertEqual(self.byte(USB_ENABLED), 1)
            self.poll_buttons(start + 4000 + delay, 3, False)
            self.assertEqual(self.byte(BRIDGE_STATE + 2), 1)
            self.assertEqual(self.byte(USB_CONTROL) & 0x90, 0)
            self.assertEqual(self.byte(USB_ENABLED), 0)
            self.assertEqual(self.byte(BRIDGE_STATE + 7), 0)

    def sample_buttons(self, time_ms, expander_pins, interrupt, read_error=0):
        def read_expander():
            if self.cpu.reg_read(UC_ARM_REG_R2) == 0:
                sp = self.cpu.reg_read(UC_ARM_REG_SP)
                buffer, = struct.unpack("<I", self.cpu.mem_read(sp, 4))
                self.cpu.mem_write(buffer, bytes([expander_pins]))
                return read_error
            return 0

        self.stubs[0x7C4C] = lambda: time_ms * 4
        self.stubs[0x7D14] = read_expander
        self.cpu.mem_write(0x20000B18, bytes([interrupt]))

    def setup_route_buttons(self, image):
        self.cpu.mem_write(0, image)
        self.stub_toggle_io()
        for addr in (0x7568, 0x7F28, 0x1133C, 0x187C8, 0x18550, 0xB3CC):
            self.stubs[addr] = lambda: 0
        self.cpu.mem_write(0x20000CAE, b"\x01")  # enable the normal key dispatcher
        self.cpu.mem_write(0x20000D4A, b"\x01")  # rocker in brightness mode
        self.cpu.mem_write(0x20000E4C, b"\x04")  # brightness between its limits
        for key in (BRIGHTNESS_PLUS, BRIGHTNESS_MINUS):
            self.cpu.mem_write(key, struct.pack("<6I", 0, 0, 0, 200, 500, 1000))

    def poll_buttons(self, time_ms, pins, interrupt=True, read_error=0):
        self.sample_buttons(time_ms, pins, interrupt, read_error)
        self.run_code(0xA9B0)

    def test_physical_holds_switch_at_deadline_without_interrupts_or_stock_hold_events(self):
        self.setup_route_buttons(self.audio_route_buttons_v2)
        self.cpu.mem_write(0x20000CAE, b"\x00")
        self.cpu.mem_write(BRIDGE_STATE + 2, b"\x01")
        for start, target, pressed in ((1000, 0, 2), (8000, 1, 1)):
            with self.subTest(target=target):
                self.calls.clear()
                for elapsed in (0, 500, 2000, 3950, 3999):
                    self.poll_buttons(start + elapsed, pressed, False)
                    self.assertEqual(self.byte(BRIDGE_STATE + 2), 1 - target)
                self.poll_buttons(start + 4000, pressed, False)
                self.assertEqual(self.byte(BRIDGE_STATE + 2), target)
                self.poll_buttons(start + 5200, pressed, False)
                self.poll_buttons(start + 5300, 3, False)
                self.assertEqual([address for address, _ in self.calls].count(0xB090), 1)

    def test_physical_holds_respect_short_release_and_invalid_expander_sample(self):
        self.setup_route_buttons(self.audio_route_buttons_v2)
        self.poll_buttons(1000, 1, False)
        self.poll_buttons(1600, 1, False)
        self.poll_buttons(4979, 1, False)
        self.poll_buttons(4999, 3, False)
        self.poll_buttons(5000, 3, False)
        self.assertEqual(self.byte(BRIDGE_STATE + 2), 0)
        self.assertIn(0x18550, [address for address, _ in self.calls])

        self.poll_buttons(6000, 1, False)
        self.poll_buttons(9999, 1, False)
        self.poll_buttons(10000, 1, False, read_error=1)
        self.assertEqual(self.byte(BRIDGE_STATE + 2), 0)
        self.poll_buttons(10020, 1, False)
        self.assertEqual(self.byte(BRIDGE_STATE + 2), 1)

    def test_physical_hold_state_starts_empty_and_preserves_atr4(self):
        self.setup_route_buttons(self.audio_route_buttons_v2)
        start = 0x2001C510
        self.cpu.mem_write(start, b"\xff" * 152)
        self.stubs[0x13B2C] = lambda: 0
        self.run_code(0x1161A, 0x1161E)
        self.assertEqual(self.cpu.mem_read(start, 152), bytes(24) + b"ATR4" + bytes(124))

    def test_physical_hold_also_switches_if_released_at_four_seconds(self):
        self.setup_route_buttons(self.audio_route_buttons_v2)
        self.poll_buttons(1000, 1, False)
        self.poll_buttons(1600, 1, False)
        self.poll_buttons(4999, 1, False)
        self.poll_buttons(5000, 3, False)
        self.assertEqual(self.byte(BRIDGE_STATE + 2), 1)
        self.assertNotIn(0x18550, [address for address, _ in self.calls])

    def test_beam_dp_advertises_lpcm_in_usb_and_dp_modes(self):
        self.assertEqual(self.beam_dp[0x155E:0x1565], bytes.fromhex("b40100 7a1f799c"))

    def press_brightness_plus(self):
        self.cpu.mem_write(0x20000D8A, b"\x04")
        self.cpu.mem_write(0x20000D8C, bytes(4))
        self.stubs[0x7C4C] = lambda: 4000 * 4
        self.run_code(0xACA6, 0xAD0A)

    def test_dp_first_addressed_startup_and_key(self):
        self.cpu.mem_write(0, self.dp_first)
        self.stub_toggle_io()
        self.cpu.mem_write(ADDRESS, struct.pack("<I", 3))
        self.cpu.mem_write(USB_ENABLED, b"\x01")
        self.status = 0  # a direct host need not present converter E08B status
        for _ in range(165):
            self.run_code(0x3140)
        self.assertEqual(self.byte(USB_ENABLED), 1)
        self.run_code(0x3140)
        self.assertEqual(self.byte(USB_ENABLED), 0)
        self.assertEqual(self.byte(BRIDGE_STATE + 2), 1)
        self.assertEqual(self.byte(SESSION), 0xA9)
        self.press_brightness_plus()
        self.assertIn(0x12648, [addr for addr, arg in self.calls])
        self.assertEqual(self.byte(BRIDGE_STATE + 2), 0)
        self.calls.clear()
        for _ in range(170):
            self.run_code(0x3140)
        self.assertEqual(self.calls, [])
        self.press_brightness_plus()
        self.assertEqual(self.byte(BRIDGE_STATE + 2), 1)

    def test_dp_first_unaddressed_and_adapter_fallback_keeps_key(self):
        self.cpu.mem_write(0, self.dp_first)
        self.stub_toggle_io()
        for addr in (0xAFC8, 0x16A3A, 0x112C8, 0x1133C):
            self.stubs[addr] = lambda: 0
        for status in (0xA1, 0xA3):
            with self.subTest(status=status):
                self.status = status
                self.cpu.mem_write(SESSION, b"\xa5")
                self.cpu.mem_write(0x20000488, struct.pack("<I", 5))  # bridge ready
                self.run_code(0x3140)
                self.assertEqual(self.byte(SESSION), 0xA7)
                self.assertEqual(self.byte(BRIDGE_STATE + 2), 1)
                self.assertEqual(self.byte(0x20000CBD), 0)
                self.assertEqual(self.byte(0x20000D92) & 8, 0)
                self.press_brightness_plus()
                self.assertEqual(self.byte(BRIDGE_STATE + 2), 0)
                self.run_code(0x3140)
                self.assertEqual(self.byte(SESSION), 0xA9)

    def test_immediate_after_address_and_manual_usb_return(self):
        self.cpu.mem_write(0, self.immediate)
        self.stub_toggle_io()
        self.cpu.mem_write(USB_ENABLED, b"\x01")
        self.status = 0
        self.run_code(0x3140)
        self.assertEqual(self.byte(SESSION), 0)
        self.assertEqual(self.byte(USB_ENABLED), 1)
        self.cpu.mem_write(ADDRESS, struct.pack("<I", 7))
        self.run_code(0x3140)
        self.assertEqual(self.byte(SESSION), 0xA9)
        self.assertEqual(self.byte(BRIDGE_STATE + 2), 1)
        self.assertEqual(self.byte(USB_ENABLED), 0)
        self.press_brightness_plus()
        self.assertIn(0x12648, [addr for addr, arg in self.calls])
        self.assertEqual(self.byte(BRIDGE_STATE + 2), 0)
        self.calls.clear()
        for _ in range(170):
            self.run_code(0x3140)
        self.assertEqual(self.calls, [])
        self.press_brightness_plus()
        self.assertEqual(self.byte(BRIDGE_STATE + 2), 1)

    def test_immediate_keeps_no_data_fallback(self):
        self.cpu.mem_write(0, self.immediate)
        for _ in range(165):
            self.run_code(0x3140)
        self.assertEqual(self.byte(SESSION), 165)
        self.run_code(0x3140, 0x3196)
        self.assertEqual(self.byte(SESSION), 166)

    def test_immediate_only_changes_first_poll_threshold_and_crc(self):
        self.assertEqual([i for i, (a, b) in enumerate(zip(self.dp_first, self.immediate))
                          if a != b], [0, 1, 2, 3, 0x1DC5A])
        self.assertEqual(self.immediate[0x1DC5A:0x1DC5C], bytes.fromhex("012c"))

    def test_dp_first_early_key_consumes_pending_preference(self):
        self.cpu.mem_write(0, self.dp_first)
        self.stub_toggle_io()
        self.cpu.mem_write(ADDRESS, struct.pack("<I", 7))
        self.press_brightness_plus()
        self.press_brightness_plus()
        self.assertEqual(self.byte(SESSION), 0xA9)
        self.assertEqual(self.byte(BRIDGE_STATE + 2), 0)
        self.calls.clear()
        self.run_code(0x3140)
        self.assertEqual(self.calls, [])

    def test_dp_first_prefers_existing_route_when_cached_or_live_dp(self):
        self.cpu.mem_write(0, self.dp_first)
        self.cpu.mem_write(ADDRESS, struct.pack("<I", 7))
        for cached, live in ((1, 1), (1, 0), (0, 1), (0, 2)):
            with self.subTest(cached=cached, live=live):
                self.cpu.mem_write(SESSION, b"\xa5")
                self.cpu.mem_write(BRIDGE_STATE + 2, bytes([cached]))
                self.stubs[0x47A6] = lambda: live
                self.run_code(0x3140)
                self.assertEqual(self.byte(SESSION), 0xA9)
                self.assertEqual(self.byte(BRIDGE_STATE + 2), cached)

    def test_dp_first_prepare_preserves_usb_and_stock_arguments(self):
        self.cpu.mem_write(0, self.dp_first)
        for op, target in ((62, 0x133CC), (110, 0x4460)):
            with self.subTest(op=op):
                self.cpu.mem_write(SESSION, b"\xa5")
                self.cpu.reg_write(UC_ARM_REG_R0, op)
                self.cpu.reg_write(UC_ARM_REG_R1, 0x20001000)
                self.cpu.reg_write(UC_ARM_REG_R2, 0x20001100)
                self.run_code(0xD84C, target)
                self.assertEqual(self.cpu.reg_read(UC_ARM_REG_R0), 0x20001000)
                self.assertEqual(self.cpu.reg_read(UC_ARM_REG_R1), 0x20001100)
                self.assertEqual(self.byte(SESSION), 0xA9)
                self.run_code(0x3140)
                self.assertEqual(self.calls, [])

    def test_dp_first_changes_only_crc_and_guarded_bytes(self):
        expected = bytearray(self.image)
        for _, offset, old, new in build_mcu.DP_FIRST_RECORDS:
            self.assertEqual(expected[offset:offset + len(old)], old)
            expected[offset:offset + len(old)] = new
        expected[:4] = self.dp_first[:4]
        self.assertEqual(expected, self.dp_first)
        self.assertEqual(sum(a != b for a, b in zip(self.image, self.dp_first)), 101)
        self.assertEqual(sum(a != b for a, b in zip(self.stock, self.dp_first)), 619)
        with self.assertRaisesRegex(SystemExit, "exact published MCU"):
            build_mcu.build_dp_first(self.stock)

    def test_crc_and_patch_guards(self):
        damaged = bytearray(self.stock)
        damaged[0x313E] ^= 1
        with self.assertRaisesRegex(SystemExit, "CRC"):
            build_mcu.verify_stock(damaged)
        build_mcu.fix_container_crc(damaged)
        with self.assertRaisesRegex(SystemExit, "SHA-256"):
            build_mcu.verify_stock(damaged)
        with self.assertRaisesRegex(SystemExit, "stock guard mismatch"):
            build_mcu.build(damaged)

    def test_dp_display_and_audio_edids(self):
        stock = (FIRMWARE / "1140").read_bytes()
        build_dp.verify_stock(stock)
        build_dp.verify_output(stock, build_dp.build(stock))
