/*
 * Out-of-tree quirk table for the renamed "snd-usb-audio-jieli" module.
 *
 * Single entry: JieLi Technology "USB Audio" dongle, USB ID 3654:5678.
 *
 * The device advertises USB Audio Class 3.0 (bInterfaceProtocol 0x30) but
 * ships broken class-specific descriptors.  On kernels without UAC3 support
 * (< 4.20, which includes L4T 4.9) the stock parser rejects it with
 * "skipping empty audio interface (v1)" and probe fails with -EINVAL.
 *
 * Here we bypass descriptor parsing entirely with QUIRK_AUDIO_FIXED_ENDPOINT
 * and describe the endpoints by hand, based on the standard interface and
 * endpoint descriptors (which are fine):
 *
 *   Playback : iface 1, altsetting 2, EP 0x03 OUT, isoc/sync, 192 B/ms
 *              => 48000 Hz, 16-bit LE, 2 channels
 *   Capture  : iface 2, altsetting 1, EP 0x83 IN,  isoc/sync,  96 B/ms
 *              => 48000 Hz, 16-bit LE, 1 channel
 *
 * .attributes = 0 means the driver sends NO pitch / sample-rate class
 * control requests (the device would STALL them); it just runs at its
 * native 48 kHz.  snd_usb_init_pitch() / set_sample_rate_v1() both early
 * -return 0 when this bit is clear.
 */
{
	USB_DEVICE(0x3654, 0x5678),
	.driver_info = (unsigned long) &(const struct snd_usb_audio_quirk) {
		.vendor_name = "JieLi Technology",
		.product_name = "USB Audio",
		.ifnum = QUIRK_ANY_INTERFACE,
		.type = QUIRK_COMPOSITE,
		.data = (const struct snd_usb_audio_quirk[]) {
			{
				.ifnum = 0,	/* AudioControl (UAC3, unusable) */
				.type = QUIRK_IGNORE_INTERFACE
			},
			{
				.ifnum = 1,	/* playback */
				.type = QUIRK_AUDIO_FIXED_ENDPOINT,
				.data = &(const struct audioformat) {
					.formats = SNDRV_PCM_FMTBIT_S16_LE,
					.channels = 2,
					.fmt_type = UAC_FORMAT_TYPE_I,
					.iface = 1,
					.altsetting = 2,
					.altset_idx = 2,
					.attributes = 0,
					.endpoint = 0x03,
					.ep_attr = USB_ENDPOINT_XFER_ISOC |
						   USB_ENDPOINT_SYNC_SYNC,
					.rates = SNDRV_PCM_RATE_48000,
					.rate_min = 48000,
					.rate_max = 48000,
					.nr_rates = 1,
					.rate_table = (unsigned int[]) { 48000 }
				}
			},
			{
				.ifnum = 2,	/* capture (mic) */
				.type = QUIRK_AUDIO_FIXED_ENDPOINT,
				.data = &(const struct audioformat) {
					.formats = SNDRV_PCM_FMTBIT_S16_LE,
					.channels = 1,
					.fmt_type = UAC_FORMAT_TYPE_I,
					.iface = 2,
					.altsetting = 1,
					.altset_idx = 1,
					.attributes = 0,
					.endpoint = 0x83,
					.ep_attr = USB_ENDPOINT_XFER_ISOC |
						   USB_ENDPOINT_SYNC_SYNC,
					.rates = SNDRV_PCM_RATE_48000,
					.rate_min = 48000,
					.rate_max = 48000,
					.nr_rates = 1,
					.rate_table = (unsigned int[]) { 48000 }
				}
			},
			{
				.ifnum = 3,	/* HID volume keys */
				.type = QUIRK_IGNORE_INTERFACE
			},
			{
				.ifnum = -1	/* terminator */
			}
		}
	}
},
