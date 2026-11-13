# DP-first audio compatibility

DP-first lets the glasses select DisplayPort audio automatically when connected directly to a phone or computer. Both the immediate and five-second variants have the same model requirements.

| Glasses | Can I install DP-first from this release? |
|---|---|
| **XREAL Air 2 Pro** | Yes. This is the only model tested with DP-first. The immediate-build playback and USB tracking results come from one pair. |
| **XREAL Air 2 (standard)** | The release flasher rejects both DP-first images on this model. The Air 2 shares the Pro’s official MCU and DP images, so the patch is a candidate for testing. Its behavior on a standard Air 2 is unverified. |
| **XREAL Air (gen 1)** | Direct-host DP-first requires a separate port because the original Air uses different firmware. Its existing automatic DP audio applies to certain converters without USB data. |

[Hardware results and known quirks](../README.md#dp-first-audio-experimental) · [Air 2 Pro build, installation and rollback guide](../xreal/air2/docs/dp-first.md)
