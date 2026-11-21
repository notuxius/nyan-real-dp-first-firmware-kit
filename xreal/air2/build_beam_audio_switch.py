"""Build the Air 2 Pro MCU and DP baselines for the Beam cover handoff."""

from . import build_dp, build_mcu
from .build_dp import hx


NO_RETRAIN_MCU_SHA256 = "2652B23314D745FB08D21CA9050AF66526E6B5969F8EC224E027E1181E717D89"
DP_SHA256 = "562BEFE8493229E6D57B31A601BF1AEAE4EA19095B7D53B29AFBF0A9AC4CE2F5"
DP_NAME = "air2-pro-dp-beam-audio-switch.bin"


# ADD A,#0xFB / JNC exit keeps checking on polls 5..255. The
# early lock check, recalibration and uint8 counter wrap still execute.
AUDIO_RELOCK_RECORD: build_dp.Record = (
    "retry a late audio PLL lock", 0x084CA, hx("6405703f"), hx("24fb503f"),
)

# The existing E0B8==1 gate calls this after the B6 retrain delay. Keep
# the stock CDR action, clear the audio phase/rate, and disable I2S output
# through the same routine used by the stock audio state machine.
AUDIO_MODE_RESET_RECORDS: tuple[build_dp.Record, ...] = (
    ("restart DP audio after B6 link retrain", 0x09188, hx("12c54b"), hx("129166")),
    ("reset cached audio phase and sample rate", 0x091A6, bytes(14),
     hx("12c54b e4 9006ea f0 a3 f0 ff 02c138")),
)


# Keep the existing pin configuration after a locked DP stream loses PLL lock.
# The bridge's ability to keep clocking and send silence in standby is unverified.
AUDIO_CLOCK_HOLD_RECORDS: tuple[build_dp.Record, ...] = (
    ("check the audio route before disabling I2S on lock loss", 0x084F5,
     hx("ff 12c138"), hx("029174 00")),
    ("leave I2S enabled on DP lock loss; preserve stock behavior on other routes",
     0x091B4, bytes(16),
     hx("90e0b8 e0 b40101 22 e4 ff 12c138 0284b9")),
)

# Preserve the last configured rate through ordinary DP standby. Reuse it
# only when the same rate returns with I²S still configured and PLL locked.
AUDIO_FAST_RESUME_RECORDS: tuple[build_dp.Record, ...] = (
    ("check whether the configured audio clock can resume without a reset", 0x0C30A,
     hx("12a97d"), hx("023430")),
    ("resume a locked same-rate DP stream or fall back to stock acquisition", 0x03470,
     bytes(108), hx(
         "12a97d 9006eae0 b40444"                  # read normalized rate, check standby phase
         "90e0b8e0 b40134"                         # clear standby state on route change
         "ef 604d b4ff02 8048"                     # absent/invalid: reset the retry counter
         "9006ebe0 6f 7049"                        # different rate: use stock acquisition
         "90d423e0 6010 90e2ebe0 20e710"           # pins configured and PLL locked
         "9006e9e0 04 f0 b40330"                   # allow two polls for the PLL to recover
         "9006eb e4f0 802c"                        # force stock acquisition on third poll
         "9006ea7403f0 8021"                       # lock recovered: resume the running phase
         "9006eae4f0a3f0 801b"                     # leave standby on a non-DP route
         "b40318 ef 7015 90e0b8e0 b4010e"          # enter standby from running DP on rate 0
         "9006ea7404f0 9006e9e4f0"                 # retain the configured rate across idle
         "7f0022 02c2cd"                           # unchanged / stock rate comparison
     )),
)

# Probe bit 4 in the stream-attribute block. The raw VB-ID offset is unknown.
AUDIO_MUTE_PROBE_RECORD: build_dp.Record = (
    "mirror bit 4 of four candidate receiver registers", 0x091C4, bytes(88), hx(
        "c0e0 c082 c083 c0d0 ee c0e0"                  # save context
        "7e00 90e0b8e0 b4012e 7ea0"                   # no receiver marker outside DP audio
        "90e730e0 30e404 ee4402fe"
        "90e735e0 30e404 ee4404fe"
        "90e736e0 30e404 ee4408fe"
        "90e737e0 30e404 ee4410fe"
        "90e085e0 5401 4e f0"                          # retain the display flag
        "d0e0 fe d0d0 d083 d082 d0e0 0248f9"            # restore context, run stock call
    ),
)


DP_RECORDS = (
    (AUDIO_RELOCK_RECORD,)
    + AUDIO_MODE_RESET_RECORDS
    + AUDIO_CLOCK_HOLD_RECORDS
    + (("sample the receiver before housekeeping", 0x0BA5E, hx("1248f9"), hx("129184")),
       AUDIO_MUTE_PROBE_RECORD)
    + AUDIO_FAST_RESUME_RECORDS
    + (("advertise LPCM on both audio routes", 0x01560, b"\x0f", b"\x00"),)
)


def build(mcu: bytes, dp: bytes) -> tuple[bytes, bytes]:
    if build_mcu.sha256(mcu) != build_mcu.AUDIO_ROUTE_BUTTONS_V2_SHA256:
        build_mcu.fail("expected the exact Air 2 Pro route-buttons v2 MCU image")
    if build_dp.sha256(dp) != build_dp.OUTPUT_SHA256:
        build_dp.fail("expected the exact kit Air 2 DP 1140 image")

    mcu_image = bytearray(mcu)
    dp_image = bytearray(dp)
    # Keep the audio route changes while skipping the link retrain in both directions.
    mcu_image[0x1862E:0x18632] = bytes.fromhex("00bf00bf")
    mcu_image[0x186B2:0x186B6] = bytes.fromhex("00bf00bf")
    for label, offset, old, new in DP_RECORDS:
        if len(old) != len(new) or dp_image[offset:offset + len(old)] != old:
            build_dp.fail(f"Beam DP guard mismatch at 0x{offset:05X}: {label}")
        dp_image[offset:offset + len(new)] = new

    build_mcu.fix_container_crc(mcu_image)
    build_dp.fix_bank0_tag(dp_image)
    build_dp.fix_container_crc(dp_image)
    build_mcu.validate_container(mcu_image, "Beam audio switch MCU")
    build_dp.validate_container(dp_image, "Beam audio switch DP")
    if build_mcu.sha256(mcu_image) != NO_RETRAIN_MCU_SHA256 or build_dp.sha256(dp_image) != DP_SHA256:
        build_mcu.fail("Beam audio switch images differ from the pinned output hashes")
    return bytes(mcu_image), bytes(dp_image)
