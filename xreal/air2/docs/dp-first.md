# Air 2 Pro DP-first audio

These experimental MCU builds target the Air 2 Pro with the kit's patched Air 2-family DP 1140 image. Both request DP audio once per MCU power-up. On an addressed USB host, the immediate variant requests the switch on the first poll and the delayed variant waits about five seconds. Actual startup and display retraining times depend on the host.

Only the Air 2 Pro has been tested with DP-first. The flasher rejects both variants on the standard Air 2 even though it shares the Pro’s stock firmware. The original Air needs a separate port for direct USB hosts. [Model compatibility](../../../docs/README.md)

After DP audio starts, hold **Brightness +** for four seconds and release to select USB audio and restore the composite USB device. The IMU, HID control, VSYNC and application updater depend on that device. Reopen an app that lost its connection. Hold the same button again to return to DP audio. A cold reconnect rearms the preference. If a powered adapter keeps the MCU running through a source change, the current choice lasts for that power session. Switching to USB on a power-only connection still requires a USB data cable and host for those interfaces to work.

## Immediate DP on a direct Motorola or ASUS connection

Choose `--dp-first-immediate` to request the stock DP transition on the **first policy poll after USB SET_ADDRESS**. This removes the five-second startup grace period on a direct USB data connection. USB addressing, EDID renegotiation and the stock amplifier startup still take time. On a connection without USB addressing the existing converter timeout, retraining and saved-gain handling still apply. An address of zero alone cannot distinguish a slow USB host from a power-only adapter.

Compared with the five-second DP-first image, this revision changes one payload byte at MCU container offset `0x1DC5A` and four CRC bytes. The Thumb instruction compares the incremented addressed-host counter with 1 instead of 166. The session latch, both route checks, the four-second key, and the MCU/DP update handlers are byte-identical. The prior five-second image was successfully flashed on the first pair, and the ASUS read its MCU and DP versions after the button restored HID. Those hardware observations apply only to that earlier image.

In a fresh checkout, complete the [Windows setup and baseline builds](motorola-audio.md#windows-build-and-offline-verification) first. Then run these commands from the repository root to build and verify the immediate image offline.

```powershell
$p = '.\.venv\Scripts\python.exe'
$stock = 'firmware/air2/09.1.00.180_20240507.bin'
$image = 'air2-pro-mcu-dp-first-immediate.bin'
& $p xreal/air2/build_mcu.py --src $stock --dp-first-immediate --out $image
& $p xreal/air2/build_mcu.py --src $stock --dp-first-immediate --verify $image
& $p xreal/mcu_flash.py --image $image --verify-only
```

For a one-pair upgrade from the five-second variant, keep the second pair unplugged. Connect the first pair directly to the ASUS, let its present DP mode settle, then hold Brightness + for four seconds to get USB. Follow the installation steps below with `$image` set to the **immediate** file above. After FINISH and JUMP_TO_APP, the fast transition may hide USB before Windows can read the version. Keep the cable connected. If the flasher prompts, hold Brightness + for four seconds once DP is active. It retries the APP check and version read only.

Test three cold reconnects to the Motorola with the same stereo clip, then check that one four-second hold on the ASUS restores the APP version read and actual tracking. After testing, the previous five-second behavior can be restored in APP mode using the same dry run and manual flash procedure with `air2-pro-mcu-dp-first.bin`. BOOT recovery still requires the exact official stock MCU.

All 20 offline tests and both flasher self-tests passed. The immediate image matched a separate rebuild and passed the flasher's exact-image verification.

The immediate image was flashed on one Air 2 Pro. The Motorola Signature played clean audio on a cold connection, but Smart Connect launched Mobile desktop despite the Experience Hub preference. The Hub remained selectable. The ASUS cold reconnect restored video without a Windows playback endpoint. All 128 bytes of the reported EDID extension matched the kit’s no-audio profile. After switching back to USB, the updater read the MCU version and the IMU delivered 32 distinct reports. Time to first sound, in-app tracking and bootloader recovery remain unverified.

## Build or verify on Windows

Run PowerShell from the repository root. To prepare your official inputs, baseline images and local Python environment, follow the [Windows environment setup and official input hash table](motorola-audio.md#windows-build-and-offline-verification) first. Keep a separate copy of the official files.

```powershell
$p = '.\.venv\Scripts\python.exe'
$stock = 'firmware/air2/09.1.00.180_20240507.bin'
$image = 'air2-pro-mcu-dp-first.bin'
& $p xreal/air2/build_mcu.py --src $stock --dp-first --out $image
& $p xreal/air2/build_mcu.py --src $stock --dp-first --verify $image
& $p xreal/mcu_flash.py --image $image --verify-only
& $p xreal/air2/build_mcu.py --src $stock --verify air2-mcu.bin
& $p xreal/air2/build_dp.py --src firmware/air2/1140 --verify air2-dp.bin
& $p -m unittest discover -s xreal/air2/tests -v
```

| Image | SHA-256 | Container CRC |
|---|---|---|
| Experimental DP-first MCU | `39819E2083911E59AEA844EE484DB52DE8B5B0C9A49BD7EC317DF80F7BD521E3` | `0x29CB1C30` |
| Immediate DP-first MCU | `DCF383BE0684FB0EBC6126CA1AD031594DB4D77F9B16BE6D5C248EA69F5769ED` | `0xCEE99416` |
| Kit USB-first MCU for rollback | `950CA9535AFBD02C40D97A167DB06ECFAEEBC35F6ADCCDED81829CC44A8BE4C9` | `0xCFB740BE` |
| Kit DP 1140 used with both MCUs | `46556947E81DD7EBBD7F26B2541B63E0362804C166C020639DA908D2ABB2F486` | `0xA86203B1` |

## First installation and hardware trial

1. Leave the second pair disconnected. Connect the test pair directly to the ASUS with a data-capable cable. Confirm from your installation records that this pair has the published kit DP build above. The version string `1140` is shared with stock. This update needs only the MCU. Close software using the glasses' HID interfaces.
2. In the PowerShell session above, run `& $p xreal/mcu_flash.py --image $image` for a **dry run**. Confirm **Air 2 Pro APP PID 0x0432**, the expected MCU version, the experimental image SHA above and the Air 2 Pro transfer plan. On the original USB-first firmware a fresh direct laptop connection supplies USB. If the pair is already running this variant, let DP audio settle, then hold Brightness + four seconds to return to USB first.
3. **Manual write**, only when ready to test this pair: `& $p xreal/mcu_flash.py --image $image --flash`. Keep the cable connected through FINISH, JUMP_TO_APP and the returning APP version check. If the transfer finished and the flasher asks for USB again, use the four-second hold after DP audio is active. The extra wait retries only device identification and the version read.
4. On the Motorola, fully unplug and reconnect the test pair without a button press. Allow the brief display retrain. Play a known stereo clip at nonzero volume. Confirm clean sound from both glasses speakers, especially the right. Repeat on three cold connections. Record failures, including silence or playback from the handset.
5. On a direct data-capable connection, hold Brightness + for four seconds after DP playback starts. On the ASUS, run `& $p xreal/mcu_flash.py` to confirm `0x0432`, control interface 4 and a successful version read. Check USB audio, the actual tracking app and host controls. Wait well beyond the startup delay and confirm USB stays available. Hold again and confirm DP audio returns. A later cold reconnect should choose DP again.
6. On the test pair, check local brightness, dimming, native RGB, your 2D refresh rates, Full-SBS 3D and the VITURE adapter as applicable. Compare with the second pair on its existing firmware. Record any repeated blackouts, loss of picture or inability to return to USB before considering a second installation.

## Future updates and rollback

Use a direct PC USB data connection. Let DP settle, select USB with the four-second hold, then dry-run the updater to confirm the APP PID. After manual selection the startup timer is consumed. MCU image transfer runs in the separate bootloader. DP updates use APP HID. The PREPARE handlers also cancel a pending automatic transition if an update starts in the initial USB window.

To restore the preceding kit behavior when APP is reachable, select USB, dry-run `& $p xreal/mcu_flash.py --image air2-mcu.bin`, then manually run `& $p xreal/mcu_flash.py --image air2-mcu.bin --flash`. The same kit DP image serves both MCUs.

If the MCU application cannot expose USB, the kit documents holding the button while connecting USB to enter BOOT. Confirm **Air 2 Pro BOOT PID 0x0431** before recovery. In BOOT the flasher allows only exact official stock. With the verified official MCU in place the manual recovery command is `& $p xreal/mcu_flash.py --restore --flash`. Once APP returns, you can restore the kit MCU or follow the [full vendor rollback instructions](../../../README.md#restore-official-xreal-air-family-firmware). After an interrupted transfer follow the kit's power-cycle procedure. Bootloader recovery has not been exercised on the DP-first test pair.

## Implementation and verification

The builder first verifies the official input, constructs the normal published MCU and checks its complete hash and all original display contracts. Seven additional guarded records produce the DP-first output. They affect 97 payload byte positions plus the four-byte CRC compared to that baseline. The variant-specific flasher entry accepts only its pinned digest for the Air 2 Pro. The normal version, PID, container, recovery-file and BOOT gates still apply.

The first hook at MCU file offset `0x0314A` sends the existing startup poll to a helper in the zero-filled slot at `0x1DC48`. When the host has assigned a USB address, the helper uses the existing count and session byte. At the deadline it marks the session complete, checks cached and live E0B8 and calls the stock button handler at `0x185E8` only when both indicate USB. This protects against toggling a host back out of DP. On an inconsistent or unexpected nonzero route the helper leaves the setting alone for that session.

Without an assigned address the helper resumes the existing E08B eligibility check. The branch at `0x0315A` uses the timed converter transition for eligible adapter attention as well. This avoids setting the original adapter-specific manual inhibit. The variant suppresses the manual-lock store at `0x0FD4E` while retaining the converter's saved-gain and delayed second-B6 behavior. These changes need physical verification on each adapter.

The key dispatch call at `0x0ACE0`, MCU PREPARE at `0x0DA5C` and DP PREPARE at `0x0DEA6` each latch the session and tail-call the original handler with its inputs intact. The DP-first build adds no nonvolatile write, USB identifier, audio register value or DP bridge patch.

Sixteen offline tests passed, including emulated keyed DP → USB → DP, early manual override, converter fallback, refusal to toggle an existing DP route, both PREPARE paths, exact patch boundaries, model and BOOT rejection, and a simulated post-transfer USB retry. Both flasher self-tests and byte-for-byte baseline rebuilds passed. Peripheral, timing and selected callee behavior are stubbed. See the immediate-build hardware results above for the limits of physical testing.
