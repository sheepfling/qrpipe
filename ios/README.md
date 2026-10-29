# qrpipe Receiver for iPhone

This app receives `qrpipe stream --protocol qrpipe` directly through the iPhone camera. It uses
Apple's QR scanner, saves verified parts in the app, and shares the assembled file through the iOS
share sheet. It has no package dependencies, remote receiver, network permission, or cloud account
for transfers.

## Install on your iPhone

1. Open [QrpipeReceiver.xcodeproj](QrpipeReceiver.xcodeproj) in Xcode.
2. Select the **QrpipeReceiver** target, choose your Apple development team under **Signing &
   Capabilities**, and select your connected iPhone as the run destination.
3. Press **Run**. Allow camera access when the app asks. Xcode may require Developer Mode on the
   phone for a locally installed app.

An Apple development team and Xcode are needed to install an unsigned custom iPhone app. They are
not involved in the transfer after installation. No App Store or hosted receiver is required.

## Transfer a file

```console
qrpipe stream --protocol qrpipe --file notes.md --open
```

For a shorter command, `qrpipe send notes.md` opens the sender page automatically. A small file
can also be saved as a looping GIF with `qrpipe send notes.md --gif notes.gif`; the phone scans
the GIF displayed on the computer in the same way. Install the local `.[gif]` extra first for GIF
export and readback.

For a larger saved transfer, `qrpipe send notes.md --html notes.html` creates a self-contained
offline sender webpage with part controls. Copy it to any desktop computer and open it in a
browser; it does not contact a server. The phone still uses this receiver app.

Open the sender page on the computer. Tap **Start scanning** in the iPhone app and point the camera
at the QR image. When the app says **Part N saved**, tap **Part saved on phone → Next** on the
computer. When all parts are verified, tap **Save or share verified file** on the phone.

Completed parts survive app restarts. If a part was interrupted before it was verified, select
that part again on the sender and rescan it. The phone and computer do not need a network or cable
connection while the QR stream is running.

The sender's current conservative defaults are 512 text characters per QR frame, 8 frames per
second, H-level QR error correction, and 256 KiB per part. A transfer is limited to 64 MiB.

## Developer checks

```console
swift test --package-path ios/ReceiverCore
xcodebuild -quiet -project ios/QrpipeReceiver.xcodeproj -scheme QrpipeReceiver \
  -sdk iphoneos -destination 'generic/platform=iOS' CODE_SIGNING_ALLOWED=NO build
```

The Swift package tests use frame vectors emitted by the Python sender. An unsigned build and
cross-language tests confirm the implementation compiles and assembles bytes; a real phone scan is
still required before depending on camera speed and reliability.
