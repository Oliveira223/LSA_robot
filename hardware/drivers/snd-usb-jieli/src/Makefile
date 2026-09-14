# Out-of-tree build of a renamed snd-usb-audio carrying a fixed-endpoint
# quirk for the JieLi 3654:5678 UAC3 dongle, for L4T 4.9 (no UAC3 support).

obj-m := snd-usb-audio-jieli.o

snd-usb-audio-jieli-y := \
	card.o \
	clock.o \
	endpoint.o \
	format.o \
	helper.o \
	mixer.o \
	mixer_quirks.o \
	mixer_scarlett.o \
	pcm.o \
	proc.o \
	quirks.o \
	stream.o

KDIR ?= /lib/modules/$(shell uname -r)/build

all:
	$(MAKE) -C $(KDIR) M=$(CURDIR) modules

clean:
	$(MAKE) -C $(KDIR) M=$(CURDIR) clean

.PHONY: all clean
