"""Offline Air 2 audio regression tests. See ../docs/motorola-audio.md for setup."""

import struct
import unittest
from pathlib import Path

from unicorn import UC_ARCH_ARM, UC_HOOK_CODE, UC_MODE_THUMB, Uc
from unicorn.arm_const import UC_ARM_REG_LR, UC_ARM_REG_PC, UC_ARM_REG_R0, UC_ARM_REG_SP

from xreal.air2 import build_dp, build_mcu


FIRMWARE = Path(__file__).resolve().parents[3] / "firmware" / "air2"
SESSION = 0x20000248
ADDRESS = 0x20000330
BRIDGE_STATE = 0x2000037A
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

    def test_manual_switch_in_both_directions(self):
        # Exercise the stock toggle and E0B8/B6 setters. Stub bus I/O, logging,
        # timing, amplifier and peripheral subroutines at their call boundaries.
        for addr in (0x9568, 0xB090, 0x59E0, 0x9EB0, 0x11410, 0x5A4C, 0x1E04,
                     0x5A70, 0x7C4C, 0x1EA4, 0x8618, 0x12648):
            self.stubs[addr] = lambda: 0
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
