# DAT SDK for iOS v0.4

## Section 1: Guides

### Setup

## Overview

The Wearables Device Access Toolkit supports iOS and Android mobile platforms, with the same OS version requirements as the Meta AI app (iOS 15.2+ and Android 10+).

Xcode 14.0+ is supported for iOS. Android Studio Flamingo or newer is supported for Android.

## Hardware requirements

Currently, the SDK supports the Ray-Ban Meta glasses (Gen 1 and Gen 2) and Meta Ray-Ban Display glasses. You can test with a simulated device using [Mock Device Kit](/docs/mock-device-kit), or directly with a device. Detailed version support of the Meta AI app and glasses firmware is located in the [Version Dependencies](/docs/version-dependencies) page.

## Setting up your glasses

To set up your glasses for development:

  1. Ensure your Meta AI app version is v254+.
  2. Ensure your glasses software is version v20+ for Ray-Ban Meta glasses or v21+ for Meta Ray-Ban Display glasses. Follow the instructions below to verify your current version.
  3. Connect your glasses to the Meta AI app.
  4. Enable developer mode (instructions below).

### Verify glasses software version

  1. In the Meta AI app, go to the Devices tab (the glasses icon at the bottom of the app), and select your device.
  2. Tap the gear icon to open **Device settings**.
  3. Tap **General** > **About** > **Version**.
  4. You should have the minimum supported version or above installed on your glasses, as outlined [here](/docs/version-dependencies).
  5. If your version is below minimum support requirements, update your glasses software.

### Enable developer mode in the Meta AI app

  1. On your iOS or Android device, select **Settings** > **App Info**, and then tap the **App version** number five times to display the toggle for developer mode.
  2. Select the toggle to enable **Developer Mode**.
  3. Click **Enable** to confirm.

  **iOS**

  ![Image of enabling developer mode on an iOS device](/images/wearables-devmode-ios.png){: width="296"}

  **Android**

  ![Image of enabling developer mode on an android device](/images/wearables-devmode-android.png){: width="296"}

### Integration overview

## Overview

The Wearables Device Access Toolkit lets your mobile app integrate with supported AI glasses. An integration establishes a session with the device so your app can access supported sensors on the user’s glasses. Users start a session from your app, and then interact through their glasses. They can:

  * Speak to your app through the device's microphones
  * Send video or photos from the device's camera
  * Pause, resume, or stop the session by tapping the glasses, taking them off, or closing the hinges
  * Play audio to the user through the device’s speakers


## Supported device

Ray-Ban Meta (Gen 1 and Gen 2) and Meta Ray-Ban Display glasses are supported by the Meta Wearables Device Access Toolkit.

## Integration lifecycle

1. **Registration**: The user connects your app to their wearable device by tapping a call-to-action in your app. This is a one‑time flow. After registration, your app can identify and connect to the user’s device when your app is open. The flow deeplinks the user to the Meta AI app for confirmation, then returns them to your app.
2. **Permissions**: The first time your app attempts to access the user's camera, you must request permission. The user can allow always, allow once, or deny. Your app deeplinks the user to the Meta AI app to confirm the requested permission, and then Meta AI returns them to your app. Microphone access uses the Hands‑Free Profile (HFP), so you request those permissions through iOS or Android platform dialogs.
3. **Session**: After registration and permissions, the user can start a session. During a session, the user engages with your app on their device.

## Sessions

All integrations with Meta AI glasses run as sessions. Only one session can run on a device at a time, and certain features are unavailable while your session is active. Users can pause, resume, or stop your session by closing the hinges, taking the glasses off (when wear detection is enabled), or tapping the glasses. Learn more in [Session lifecycle](/docs/lifecycle-events).

## Key components

`MWDATCore` is the foundation for your integration. It handles:
- App registration with the user’s device and registration state
- Device discovery and management
- Permission requests and state management
- Telemetry

`MWDATCamera` handles camera access and:
- Resolution and frame rate selection
- Starting a video stream and sending/listening for pause, resume, and stop signals
- Receiving frames from devices
- Capturing a single frame during a stream and delivering it to your app
- Photo format

For more, check out our **API reference documentation**: [iOS](https://wearables.developer.meta.com/docs/reference/ios_swift/dat/0.4), [Android](https://wearables.developer.meta.com/docs/reference/android/dat/0.4).

### Microphones and speakers

Use mobile platform functions to access the device over Bluetooth. To use the device's microphones for input, use HFP (Hands-Free Profile). Audio is streamed as 8 kHz mono from the device to your app.

### App management

After registration, your app appears in the user’s App Connections list in the Meta AI app, where permissions can be unregistered or managed.

## Next steps

- See real-world integration concepts on [our blog](https://developers.meta.com/blog/introducing-meta-wearables-device-access-toolkit/).
- Start building your first integration with our step‑by‑step guides for [iOS](/docs/build-integration-ios) and [Android](/docs/build-integration-android).

### Integrate Wearables Device Access Toolkit into your iOS app

## Overview

This guide explains how to add Wearables Device Access Toolkit registration, streaming, and photo capture to an existing iOS app. For a complete working sample, compare with the [provided sample app](https://github.com/facebook/meta-wearables-dat-ios/tree/main/samples).

## Prerequisites

Complete the environment, glasses, and GitHub configuration steps in [Setup](/docs/getting-started-toolkit).

Your integration must use a registered bundle identifier. To register or manage bundle IDs, see Apple's [Register an App ID](https://developer.apple.com/help/account/identifiers/register-an-app-id/) and [Bundle IDs](https://developer.apple.com/documentation/appstoreconnectapi/bundle-ids) documentation.

## Step 1: Add info properties

In your app's `Info.plist` or using Xcode UI, insert the required keys so the Meta AI app can callback to your app and discover the glasses. `AppLinkURLScheme` is required so that the Meta AI app can callback to your application. The example below uses `myexampleapp` as a placeholder. Adjust the scheme to match your project.

Add the `MetaAppID` key to provide the Wearables Device Access Toolkit with your application ID - omit or use `0` for it if you are using Developer Mode.
Published apps receive a dedicated value (see [Manage projects](/docs/manage-projects)) from the Wearables Developer Center.

**Note**: If you pre-process `Info.plist`, the `://` suffix will be stripped unless you add the `-traditional-cpp` flag. See [Apple Technical Note TN2175](https://developer.apple.com/library/archive/technotes/tn2175/_index.html#//apple_ref/doc/uid/DTS10004415-CH1-TNTAG3).

```xml
<!-- Configure custom URL scheme for Meta AI callbacks -->
<key>CFBundleURLTypes</key>
<array>
  <dict>
    <key>CFBundleTypeRole</key>
    <string>Editor</string>
    <key>CFBundleURLName</key>
    <string>$(PRODUCT_BUNDLE_IDENTIFIER)</string>
    <key>CFBundleURLSchemes</key>
    <array>
      <string>myexampleapp</string>
    </array>
  </dict>
</array>

<!-- Allow Meta AI (fb-viewapp) to call the app -->
<key>LSApplicationQueriesSchemes</key>
<array>
  <string>fb-viewapp</string>
</array>

<!-- External Accessory protocol for Meta Wearables -->
<key>UISupportedExternalAccessoryProtocols</key>
<array>
  <string>com.meta.ar.wearable</string>
</array>

<!-- Background modes for Bluetooth and external accessories -->
<key>UIBackgroundModes</key>
<array>
  <string>bluetooth-peripheral</string>
  <string>external-accessory</string>
</array>
<key>NSBluetoothAlwaysUsageDescription</key>
<string>Needed to connect to Meta Wearables</string>

<!-- Wearables Device Access Toolkit configuration -->
<key>MWDAT</key>
<dict>
  <key>AppLinkURLScheme</key>
  <string>myexampleapp://</string>
  <key>MetaAppID</key>
  <string>0</string>
</dict>
```

## Step 2: Add the SDK Swift package

Add the SDK through Swift Package Manager.

1. In Xcode, select **File** > **Add Package Dependencies...**
1. Search for `https://github.com/facebook/meta-wearables-dat-ios` in the top right corner.
1. Select `meta-wearables-dat-ios`.
1. Set the version to one of the [available versions](https://github.com/facebook/meta-wearables-dat-ios/tags).
1. Click **Add Package**.
1. Select the target to which you want to add the package.
1. Click **Add Package**.

Import the required modules in any Swift files that use the SDK.

```swift
import MWDATCamera
import MWDATCore
```

## Step 3: Initialize the SDK

Call [`Wearables.configure()`](https://wearables.developer.meta.com/docs/reference/ios_swift/dat/0.4/mwdatcore_wearables#functions) once when your app launches.

```swift
func configureWearables() {
  do {
    try Wearables.configure()
  } catch {
    assertionFailure("Failed to configure Wearables SDK: \(error)")
  }
}
```

## Step 4: Launch registration from your app

Register your application with the Meta AI app either at startup or when the user wants to turn on your wearables integration.

```swift
func startRegistration() throws {
  try Wearables.shared.startRegistration()
}

func startUnregistration() throws {
  try Wearables.shared.startUnregistration()
}

func handleWearablesCallback(url: URL) async throws {
  _ = try await Wearables.shared.handleUrl(url)
}
```

Observe registration and device updates.

```swift
let wearables = Wearables.shared

Task {
  for await state in wearables.registrationStateStream() {
    // Update your registration UI or model
  }
}

Task {
  for await devices in wearables.devicesStream() {
    // Update the list of available glasses
  }
}
```

## Step 5: Manage camera permissions

Check permission status before streaming and request access if necessary.

```swift
var cameraStatus: PermissionStatus = .denied
...
cameraStatus = try await wearables.checkPermissionStatus(.camera)
...
cameraStatus = try await wearables.requestPermission(.camera)
```

## Step 6: Start a camera stream

Create a [`StreamSession`](https://wearables.developer.meta.com/docs/reference/ios_swift/dat/0.4/mwdatcamera_streamsession), observe its state, and display frames. You can use an auto device selector to make smart decision for the user to select a device. This example uses [`AutoDeviceSelector`](https://wearables.developer.meta.com/docs/reference/ios_swift/dat/0.4/mwdatcore_autodeviceselector) to make a decision for the user. Alternatively, you can use a specific device selector, [`SpecificDeviceSelector`](https://wearables.developer.meta.com/docs/reference/ios_swift/dat/0.4/mwdatcore_specificdeviceselector), if you provide a UI for the user to select a device.

You can request resolution and frame rate control using [`StreamSessionConfig`](https://wearables.developer.meta.com/docs/reference/ios_swift/dat/0.4/mwdatcamera_streamsessionconfig). Valid `frameRate` values are `2`, `7`, `15`, `24`, or `30` FPS. [`resolution`](https://wearables.developer.meta.com/docs/reference/ios_swift/dat/0.4/mwdatcamera_streamingresolution) can be set to:

- `high`: 720 x 1280
- `medium`: 504 x 896
- `low`: 360 x 640

[`StreamSessionState`](https://wearables.developer.meta.com/docs/reference/ios_swift/dat/0.4/mwdatcamera_streamsessionstate) transitions through `stopping`, `stopped`, `waitingForDevice`, `starting`, `streaming`, and `paused`.

Register callbacks to collect frames and state events.

```swift
// Let the SDK auto-select from available devices
let deviceSelector = AutoDeviceSelector(wearables: wearables)
let config = StreamSessionConfig(
  videoCodec: VideoCodec.raw,
  resolution: StreamingResolution.low,
  frameRate: 24)
streamSession = StreamSession(streamSessionConfig: config, deviceSelector: deviceSelector)

let stateToken = session.statePublisher.listen { state in
  Task { @MainActor in
    // Update your streaming UI state
  }
}

let frameToken = session.videoFramePublisher.listen { frame in
  guard let image = frame.makeUIImage() else { return }
  Task { @MainActor in
    // Render the frame in your preview surface
  }
}

Task { await session.start() }
```

Resolution and frame rate are constrained by the Bluetooth Classic connection between the user’s phone and their glasses. To manage limited bandwidth, an automatic ladder reduces quality as needed. It first lowers the resolution by one step (for example, from High to Medium). If bandwidth remains constrained, it then reduces the frame rate (for example, 30 to 24), but never below 15 fps.

The image delivered to your app may appear lower quality than expected, even when the resolution reports “High” or “Medium.” This is due to per‑frame compression that adapts to available Bluetooth Classic bandwidth. Requesting a lower resolution, a lower frame rate, or both can yield higher visual quality with less compression loss.

## Step 7: Capture and share photos

Listen for [`photoDataPublisher`](https://wearables.developer.meta.com/docs/reference/ios_swift/dat/0.4/mwdatcamera_streamsession#properties) events and handle the returned [`PhotoData`](https://wearables.developer.meta.com/docs/reference/ios_swift/dat/0.4/mwdatcamera_photodata). Then, when a stream session is active, call [`capturePhoto`](https://wearables.developer.meta.com/docs/reference/ios_swift/dat/0.4/mwdatcamera_streamsession#functions).

```swift
_ = session.photoDataPublisher.listen { photoData in
  let data = photoData.data
  // Convert to UIImage or hand off to your storage layer
}

session.capturePhoto(format: .jpeg)
```

## Next steps

- See details on permission flows in [Permissions and registration](/docs/permissions-requests).
- See details on session lifecycles in [Session lifecycle](/docs/lifecycle-events).
- Test without a device with [Mock Device Kit](/docs/testing-mdk-ios).
- Compare against the [iOS sample app](https://github.com/facebook/meta-wearables-dat-ios/tree/main/samples).
- Prepare for release with [Manage projects](/docs/manage-projects) and [Set up release channels](/docs/set-up-release-channels) in the Wearables Developer Center.

### Session lifecycle

## Overview

The Wearables Device Access Toolkit runs work inside sessions. Meta glasses expose two experience types:

- **Device sessions** grant sustained access to device sensors and outputs.
- **Transactions** are short, system-owned interactions (for example, notifications or "Hey Meta").

When your app requests a device session, the glasses grant or revoke access as needed, the app observes state, and the system decides when to change it.

## Device session states

`SessionState` is device-driven and delivered asynchronously through `StateFlow`.

| State              | Meaning                                   | App expectation                       |
|--------------------|--------------------------------------------|---------------------------------------|
| `STOPPED`          | Session is inactive and not reconnecting.  | Free resources. Wait for user action. |
| `RUNNING`          | Session is active and streaming data.      | Perform live work.                    |
| `PAUSED`           | Session is temporarily suspended.          | Hold work. Paths may resume.          |

**Note:** `SessionState` does not expose the reason for a transition.

## Observe device session transitions

Use the SDK flow to track `SessionState` and react without assuming the cause of a change. For an Android integration:

```kotlin
Wearables.getDeviceSessionState(deviceId).collect { state ->
    when (state) {
        SessionState.RUNNING -> onRunning()
        SessionState.PAUSED -> onPaused()
        SessionState.STOPPED -> onStopped()
    }
}
```

Recommended reactions:

- On `RUNNING`, confirm UI shows that the device session is live.
- On `PAUSED`, keep the connection and wait for `RUNNING` or `STOPPED`.
- On `STOPPED`, release device resources and allow the user to restart.

## Common device session transitions

The device can change `SessionState` when:

- The user performs a system gesture that opens another experience.
- Another app or system feature starts a device session.
- The user removes or folds the glasses, disconnecting Bluetooth.
- The user removes the app from the Meta AI companion app.
- Connectivity between the companion app and the glasses drops.

Many events lead to `STOPPED`, while some gestures pause a session and later resume it.

## Pause and resume

When `SessionState` changes to `PAUSED`:

- The device keeps the connection alive.
- Streams stop delivering data while paused.
- The device resumes streaming by returning to `RUNNING`.

Your app should not attempt to restart a device session while it is paused.

## Device availability

Use device metadata to detect availability. Hinge position is not exposed, but it influences connectivity.

```kotlin
Wearables.devicesMetadata[deviceId]?.collect { metadata ->
    if (metadata.available) {
        onDeviceAvailable()
    } else {
        onDeviceUnavailable()
    }
}
```

Expected effects:

- Closing the hinges disconnects Bluetooth, stops active streams, and forces `SessionState` to `STOPPED`.
- Opening the hinges restores Bluetooth when the glasses are nearby, but does not restart the device session. Start a new session after `metadata.available` becomes `true`.

## Implementation checklist

- Subscribe to `getDeviceSessionState` and handle all `SessionState` values.
- Monitor `devicesMetadata` for availability before starting work.
- Release resources only after receiving `STOPPED` or loss of availability.
- Avoid inferring transition causes. Instead, rely only on observable state.

### Permissions and registration

## Overview

The Wearables Device Access Toolkit separates app registration and device permissions. All permission grants occur through the Meta AI app. Permissions work across multiple linked wearables.

Camera permissions are granted at the app level. However, each device will need to confirm permissions specifically, in turn allowing your app to support a set of devices with individual permissions.

To create an integration, follow this guidance to build your first integration for [Android](/docs/build-integration-android) or [iOS](/docs/build-integration-ios).

## Registration

Your app registers with the Meta AI app to be an permitted integration. This establishes the connection between your app and the glasses platform. Registration happens once through Meta AI app with glasses connected. Users see your app name in the list of connected apps. They can unregister anytime through the Meta AI app. You can also implement an unregistration flow is desired.

## Device permissions

After registration, request specific permissions (see possible values for [Android](https://wearables.developer.meta.com/docs/reference/android/dat/0.4/com_meta_wearable_dat_core_types_permission#enumeration_constants) and [iOS](https://wearables.developer.meta.com/docs/reference/ios_swift/dat/0.4/mwdatcore_permission#enumeration_constants)). The Meta AI app runs the permission grant flow. Users choose **Allow once** (temporary) or **Allow always** (persistent).

### User experience flow

![Illustrating the user experience flow for permissions and using features.](/images/wearables-permissions-request-1.png)

- Without registration, permission requests fail.
- With registration but no permissions, your app connects but cannot access camera.

## Multi-device permission behavior

Users can link multiple glasses to Meta AI. The toolkit handles this transparently.

### How it works

Users can have multiple pairs of glasses. Permission granted on any linked device allows your app to use that feature. When checking permissions, Wearables Device Access Toolkit queries all connected devices. If any device has the permission granted, your app receives "granted" status.

### Practical implications

You don't track which specific device has permissions. Permission checks return granted if _any_ connected device has approved. If all devices disconnect, permission checks will indicate unavailability. Users manage permissions per device in the Meta AI app.

## Distribution and registration

Testing vs. production have different permission requirements. When developer mode is activated, registration is always allowed. When a build is distributed, users must be in the proper release channel to get the app. This is controlled by the `MWDAT` application ID.

- For setting up developer mode, see [Getting started with the Wearables Device Access Toolkit](/docs/getting-started-toolkit).
- For details on creating release channels, see [Manage projects in Developer Center](/docs/manage-projects).
  - This page also explains where to find the `APPLICATION_ID` that must be added to your production manifest/bundle configuration.

### Use device microphones and speakers

## Overview

Device audio uses two Bluetooth profiles:

- A2DP (Advanced Audio Distribution Profile) for high‑quality, output‑only media
- HFP (Hands‑Free Profile) for two‑way voice communication

## Integrating sessions with HFP

Wearables Device Access Toolkit sessions share microphone and speaker access with the system Bluetooth stack on the glasses.
## iOS sample code

```swift
// Set up the audio session
let audioSession = AVAudioSession.sharedInstance()
try audioSession.setCategory(.playAndRecord, mode: .default, options: [.allowBluetooth])
try audioSession.setActive(true, options: .notifyOthersOnDeactivation)
```

**Note:** When planning to use HFP and streaming simultaneously, ensure that HFP is fully configured before initiating any streaming session that requires audio functionality.

```swift
func startStreamSessionWithAudio() async {
  // Set up the HFP audio session
  startAudioSession()

  // Instead of waiting for a fixed 2 seconds, use a state-based coordination that waits for HFP to be ready
  try? await Task.sleep(nanoseconds: 2 * NSEC_PER_SEC)

  // Start the stream session as usual
  await streamSession.start()
}
```

## Android sample code

```kotlin
private fun routeAudioToBluetooth() {
  val audioManager = context.getSystemService(Context.AUDIO_SERVICE) as AudioManager

  // Get list of currently available devices
  val devices = audioManager.availableCommunicationDevices

  // User chooses one of the devices from the list
  val userSelectedDeviceType = AudioDeviceInfo.TYPE_BLUETOOTH_SCO

  var selectedDevice: AudioDeviceInfo? = null
  for (device in devices) {
    if (device.type == userSelectedDeviceType) {
      selectedDevice = device
      break
    }
  }

  if (selectedDevice != null) {
    audioManager.mode = AudioManager.MODE_NORMAL
    audioManager.setCommunicationDevice(selectedDevice)
  }
}
```

For guidance on how to use audio in your app, refer to the corresponding iOS API and Android API docs:

- iOS API: [AVAudioSession](https://developer.apple.com/documentation/AVFAudio/AVAudioSession)

- Android API: [AudioManager](https://developer.android.com/reference/android/media/AudioManager)

### Mock Device Kit

## Overview

Mock Device Kit is a component of the Device Access Toolkit that helps you build and test integrations for Meta glasses, without the need to access the actual hardware.

This kit provides a simulated device that mirrors the capabilities and behavior of Meta glasses, including camera, media streaming, permissions, and device state changes. You can use it to test your app integrations in a virtual environment. This is useful for rapid iteration, automated testing, and development workflows where physical devices may not be available or practical to use.

**Note:** This page demonstrates how the Mock Device Kit is used in the CameraAccess sample. For information on using Mock Device Kit APIs in your own testing, see [Android testing with Mock Device Kit](/docs/testing-mdk-android) or [iOS testing with Mock Device Kit](/docs/testing-mdk-ios).

## Mock Device Kit in the CameraAccess sample

To connect to a simulated device using the sample app:

1. Tap the **Debug icon** on your mobile device. You will see the Mock Device Kit menu open.
2. Tap **Pair RayBan Meta**. A Mock Device card is then added to the view.
3. Swipe down the **Mock Device Kit** menu. The new device should now be available.

    ![Image showing how to connect Mock Device Kit](/images/mock-device-kit-connecting-to.png){:width="60%"}

## Changing state

Now that your mock device is paired, you can alter the state of your virtual device:

- To simulate powering on the glasses, tap **PowerOn**. The device must change to "Connected" on the main screen.
- To simulate unfolding the glasses, tap **Unfold**. The device is now ready for streaming.
- To simulate putting on the glasses, tap **Don**.

**Note**: CameraAccess automatically checks camera permissions when you start streaming. If permission isn't granted, the app redirects to Meta AI to complete the flow.

## Simulating media streaming

To test your app's media handling capabilities, you can configure the Mock Device Kit with sample media files that simulate video streaming and photo capture from the glasses.

### Streaming video

1. Set your mock device to **Unfold**.
2. Click **Select video** and select any supported video. This video will be used as mock streaming video.

    **Note**: Android doesn’t transcode video automatically. Any video used here must be in h265 format. To transcode a video to h265, you can use [FFmpeg](https://www.ffmpeg.org/). For example:

    ```bash
    ffmpeg -hwaccel videotoolbox -i input_video.mp4 -c:v hevc_videotoolbox -c:a aac_at -tag:v hvc1 -vf "scale=540:960" output_video.mov
    ```

### Image capture

1. Tap **Select image** and select any supported photo. This photo will be used as a mock capture result.
2. Go to the main screen, navigate to the device, and start streaming. You can try capture here as well.

### How to test with Mock Device Kit on iOS

## Overview

Use this guide when your iOS project already integrates the Wearables Device Access Toolkit and you need to test without physical glasses.

## Set up Mock Device Kit in XCTest

Create a reusable base rule or test class that configures Mock Device Kit, grants permissions, and resets state.

```swift
import XCTest
import MetaWearablesDAT

@MainActor
class MockDeviceKitTestCase: XCTestCase {
    private var mockDevice: MockRaybanMeta?
    private var cameraKit: MockCameraKit?

    override func setUp() async throws {
        try await super.setUp()

        try? Wearables.configure()
        mockDevice = MockDeviceKit.shared.pairRaybanMeta()
        cameraKit = mockDevice?.getCameraKit()
    }

    override func tearDown() async throws {
        MockDeviceKit.shared.pairedDevices.forEach { device in
            MockDeviceKit.shared.unpairDevice(device)
        }
        mockDevice = nil
        cameraKit = nil
        try await super.tearDown()
    }
}
```

## Configure camera feeds for streaming tests

Mock camera feeds let you verify streaming and capture workflows without video hardware.

### Provide a mock video feed

```swift
guard let device = MockDeviceKit.shared.pairRaybanMeta() else { return }
let camera = device.getCameraKit()
await camera.setCameraFeed(fileURL: videoURL)
```

### Provide a captured photo

```swift
guard let device = MockDeviceKit.shared.pairRaybanMeta() else { return }
let camera = device.getCameraKit()
await camera.setCapturedImage(fileURL: imageURL)
```

### Onboarding and organization management

Wearables Developer Center manages the full lifecycle of wearables integrations,
from development and testing to app sharing. It oversees integration projects,
versions, and release channels. To manage your team and projects effectively,
you need to understand organizational roles and account requirements. This guide
explains how to set up and manage your organization, team, and members.

## One organization per company

**Important:** Each company must have only **one** Managed Meta Account (MMA)
organization in [Admin Center.](https://work.meta.com/admin/work_tools_overview)
**Do not create a new MMA organization if one already exists for your company.**
Check with your IT, engineering lead, or project manager before proceeding.

## Key terms

| Term                           | Definition                                                                     |
| ------------------------------ | ------------------------------------------------------------------------------ |
| **Managed Meta Account (MMA)** | A Meta account managed by an organization admin for secure access and control. |
| **Admin Center**               | A portal for managing IT tasks related to people management and security.      |
| **Organization**               | Represents your company in Admin Center                                        |
| **Team**                       | A group within Wearables Developer Center representing your project team.      |

## Set up your organization and team

### 1. Check for an existing MMA Organization

- **Before you start:** Ask your company's IT, engineering lead, or project
  manager if an MMA organization already exists in Admin Center.

### 2. Designate an organization admin

- Only one person (ideally IT, engineering lead, or project manager) should
  create the MMA organization for your company.
- This person becomes the MMA organization admin and manages membership for all
  contributors. You can change
  [admin roles in Admin Center](https://work.meta.com/help/632623761283671) if
  needed.

### 3. Create the MMA organization in Admin Center

- The admin should sign up to Wearables Developer Center. During this process
  they will be redirected to the MMA setup in
  [work.meta.com](https://work.meta.com/).
- Use your company’s official name for the organization.

![Screenshot of Wearables Developer Center login screen](start-in-wdc.png)

### 4. Invite developers to the MMA organization

- The admin invites all developers and contributors who need access to Wearables
  Developer Center.
- Invited members will receive prompts to create or link their MMA.
- **Note:** Only members of your company’s MMA organization can join your
  Wearables Developer Center team.

### 5. Access Wearables Developer Center

- Once your MMA and organization membership are set up, log into Wearables
  Developer Center with your MMA credentials.

### 6. Invite team members in Wearables Developer Center

- You will have a default personal team when you first log into Wearables
  Developer Center with your MMA account. You can create a new team and/or add
  people to your existing team. You don't have to be admin in Admin Center to do
  this.
- Only developers in the same MMA organization can join Wearables Developer
  Center team.
- Use the **Invite Member** process. If invitees lack an MMA, they will be
  prompted to create one and join the organization.

## Get started in Wearables Developer Center

Use Wearables Developer Center to:

- Create and manage AI glasses projects, including device permissions and
  connectivity.
- Manage integration versions (Major, Minor, Patch).
- Invite testers to
  [release channels](https://wearables.developer.meta.com/docs/set-up-release-channels).

## Invite team members: admin rights

- **If you're an admin in Admin Center:**
  - **Option 1:** Set up the new member's MMA first, then invite them via
    Wearables Developer Center.
  - **Option 2:** Invite directly from Wearables Developer Center; if the person
    lacks an MMA, they will receive an email to create one.
- **If you are not an admin:**
  - You can only invite people who already have an MMA. Organizational setup
    remains the administrator's responsibility.

## Add, remove, or leave a team

**Add a member:**

1. Select your team from the dropdown menu in the top left of Wearables
   Developer Center.
2. Click **Settings** in the left menu.
3. Click **Invite member**.
4. Enter the member’s email (must be linked to a Managed Meta account).
5. Click **Add**. The invitee receives an invitation email.

**Remove a member:**

1. Select your team from the dropdown menu in the top left.
2. Click **Settings** in the left menu.
3. Find the member below **Members** and click **Remove member**.
4. Confirm by clicking **Remove**.

**Leave a team:**

1. Select your team from the dropdown menu in the top left.
2. Click **Settings** in the left menu.
3. Next to your name below **Members**, click the delete icon.
4. Confirm by clicking **Remove member**.

### Manage projects

Once you have
[onboarded](https://work.meta.com/signup/landing/wearables_dev/?signup_code=Ae4HLaAAGqKWzrY23IYi8Sjdo3KDeP8pnrwduotfeJOYUwBXSjK1krB7t6vXxtqXavXI5bfIjnLeIEb6g3piWHM51GCndj6kRSE&request_id=filcemlbbnolpolgkigehjpjkofhkogdkheckidi),
you can create a project or manage existing ones in
[Meta Wearables Developer Center](https://work.meta.com/signup/landing/wearables_dev/?signup_code=Ae4HLaAAGqKWzrY23IYi8Sjdo3KDeP8pnrwduotfeJOYUwBXSjK1krB7t6vXxtqXavXI5bfIjnLeIEb6g3piWHM51GCndj6kRSE&request_id=filcemlbbnolpolgkigehjpjkofhkogdkheckidi).

## Projects

You can create new projects or manage your existing ones directly in the
Wearables Developer Center.

## Create a project

1. Click **New Project**.
2. Give your project a name (what you want to call it) and a brief description
   (what it does).

## App configuration

You can connect your own mobile apps with your wearable device (for example,
Meta AI glasses) by defining the app details. Click **App configuration**.

1. Add the requested details for a mobile app you want to integrate with Meta
   wearable devices.

### Application ID integration

To register your application successfully (without using Developer Mode), you
must include the Wearables Application ID in your app’s manifest and pass it in
the registration call. Copy and paste the integration details into your iOS or
Android application build to complete this step.

## Product listing

**App name and icon**

- You need to provide your app's name and an icon.
- The icon must be in PNG or JPEG format.
- Separate icons for dark and light mode are supported.
- The maximum supported dimensions for the icon are 200x200 pixels.

These details will also be visible to other users in the Meta AI app when they
[adjust permissions](https://wearables.developer.meta.com/docs/set-up-release-channels#manage-permissions-for-connected-apps).

## Permissions

If your app or project needs access to device functionality like the camera, you
must provide a justification in the **Permissions** tab. This justification is
for Meta’s internal review only and is not shown to end-users. Reviewers use
your explanation to determine if the permission is necessary and appropriate for
your app’s functionality.

> **Note:** Currently, the only permission is camera, but new device
> capabilities will be added in future iterations.

## Distribute

When you’re ready for people to try your project, you need to
[set up release channels](https://wearables.developer.meta.com/docs/set-up-release-channels).

### Set up versions and release channels in Meta Wearables Developer Center

Effectively manage how you distribute and test Meta integrations by setting up versions and release channels in the Meta Wearables Developer Center. This guide walks you through best practices and step-by-step instructions to help you roll out updates, gather meaningful feedback, test features safely, and maintain integration quality.

## Understand versions

Wearables Developer Center uses a versioning system that helps track changes and maintain stability across your integrations. Each version details product specifics, including the name, icon, and any edits to permission requests or app configuration.

After you add and save these details you can find them by going to **Distribute > Version details > Project data**.


When you change any of these details, you need to create a new version of the integration so you can distribute it to testers on a release channel.

When selecting the version to use, the type of change you are making determines the category you should choose:

- **Major (e.g., 2.3.4 to 3.0.0):** Choose this for significant changes or API revisions that are not guaranteed to maintain compatibility with previous versions. For example, select a major version if you change core app functionality in a way that breaks existing features.
- **Minor (e.g., 2.3.4 to 2.4.0):** Select a minor version when introducing new features while still maintaining backwards compatibility. For example, if you add a new button or feature.
- **Patch (e.g., 2.3.4 to 2.3.5):** Use a patch version for fixing bugs or delivering minor improvements that do not break compatibility, such as correcting a typo or a small bug fix.

## Create versions

To create a new version of your integration:

1. Log in to the [Meta Wearables Developer Center](https://wearables.developer.meta.com/).
2. Select your project from the dashboard.
3. Go to the **Distribute** menu and choose **Versions**.
4. Click **+ New version**.
5. Select your version type (**Major, Minor, or Patch**).
6. Click **Create version**.

## About release channels

Release channels let you control distribution of your versions. By creating and assigning versions to specific channels, you determine which user groups access each version. Each channel supports only one version at a time, but you can attach the same version to multiple channels if needed.

### Release channel options

- **Invite-only channels:** Useful for alpha/beta testing. All release channels for Device Access Toolkit are currently invite-only.
- **User invitations by email:** You can only invite testers who have [Meta accounts](https://developers.meta.com/horizon/blog/introducing-meta-accounts-what-developers-need-to-know/). Make sure to add the email associated with the tester’s Meta account when prompted to invite testers.
- **Tester autonomy:** Testers may accept or decline invitations and can remove themselves at any time.
- **Developer control:** You can revoke tester access at any point. You can also reinvite users you have previously revoked.
- **Limitations:** Up to 3 channels per integration, max 100 users per channel.

## Create a release channel

To set up a new release channel:

1. In the **Distribute** menu, click **Release channels** (right menu, adjacent to **Versions**).
2. Select **+ Create a release channel**.
3. Enter a unique **Name** and a clear **Description** for your channel. Click **Next**.
4. Select the **Version** you wish to distribute. You can update this selection whenever needed. Click **Next**.
5. Enter the email addresses of the testers you wish to invite.
   **Note:** These must be emails for already existing Meta Accounts (this is different from a Meta Managed Account). If the tester needs a Meta Account, they can create one at [meta.ai](http://meta.ai/) or by logging into the Meta AI app.
6. Click **Next**.
7. Review your selections, then click **Create release channel** to confirm. If you do not confirm by clicking this button, users will not receive the invitation.

## Manage test user access

Testers can belong to multiple release channels for one integration, such as for regression or parallel testing. Each invited tester must accept the email invitation to join a test group. Developers can remove testers, and testers can leave at any time.

**Note:** Release channels control a user's ability to register an app integration. Removing a user from a channel after they've registered will not unregister the connected app for Meta AI and the wearable device.

To view release channel details and manage test users, click **Edit** next to the channel. From here, you can also change the distributed version.

Test users can view the integrations they are testing at: [https://wearables.meta.com/invites](https://wearables.meta.com/invites)

## Manage permissions and switch release channels in the Meta AI app

People testing your integration can manage app permissions and switch release channels for your devices and connected apps in the Meta AI app. These settings help you control what your connected apps can access and allow you to try new features by joining different release channels.

## Manage permissions for connected apps

As a test user, managing permissions lets you control what each integration can access on your device.

To manage permissions:

1. Open the Meta AI App.
2. Go to the device menu and tap **Settings**.
3. Select **Connected Apps** to see a list of all apps linked to your Meta AI account.
4. Tap on an app to view its permissions.
5. Adjust specific permissions, e.g., for the camera:
    - You may see options like:
        - Always allow
        - Always ask
        - Don’t allow
6. Click **Confirm** to save your changes.

**Note:** Changes made to these settings will apply to all devices connected to your Meta AI app.

## Switch release channel

Release channels let testers choose between different versions of your integration.

### To switch release channel

1. Open the Meta AI App.
2. Go to the device menu and tap **Settings**.
3. Tap **Release Channel** to see available options.
4. Select your preferred channel.
    - If there are multiple channels, you can pick the one you want.
    - If only one is available, it will be selected by default.
5. Click **Confirm** to save your changes.

![switch release channel](/images/switch-release-channel.gif)

Learn [How to disconnect apps from AI glasses](https://www.meta.com/en-gb/help/ai-glasses/836668612353969/).

### Access your Meta Wearables Device Access Toolkit information

You can request a copy of your information related to Wearables Device Access Toolkit integrations. You will get a file containing your profile information and other data.

To request your information, follow these steps:

1. Visit the [Download information from your managed Meta account](https://work.meta.com/help/836165424150596) help article.
2. Follow the instructions to request an export of your **Meta Wearables developer profile information**.

![Download your information.](/images/download-your-information.png)

We will email you to acknowledge your request. You will get another email when your files are ready to download.

### Known issues

## Wearables Device Access Toolkit

| Issue | Workaround |
| --- | --- |
| If there isn't an internet connection present, your app may fail to connect with the Wearables Developer Access Toolkit, and you may not be able to register your app in developer mode. | An internet connection is required for registration. |
| Streams that are started with the glasses doffed are paused when they glasses are donned. | None at this time. You can unpause by tapping the side of your glasses. |
| [`DeviceStateSession`](https://wearables.developer.meta.com/docs/reference/ios_swift/dat/0.4/mwdatcore_devicestatesession) (iOS) and [`DeviceSession`](https://wearables.developer.meta.com/docs/reference/android/dat/0.4/com_meta_wearable_dat_core_session_devicesession) (Android) are not reliable in combination with a camera stream session. | Avoid using `DeviceStateSession` (iOS) and `DeviceSession` (Android) at this time. Their omission will not affect camera functionality. |
| **[iOS-only]** Meta Ray-Ban Display glasses don't play "Experience paused"/"Experience started" when pausing or resuming the session using captouch gestures. | This issue will be resolved in a future SDK release. |

## Wearables Developer Center

| Issue | Workaround |
| --- | --- |
| Email addresses of members invited to a release channel must already be associated with a Meta account. | Verify anyone you invite to a release channel has set up a Meta account at [meta.ai](https://www.meta.ai/). |
| Users logged into [developers.meta.com](https://developers.meta.com/) (Meta Horizon) may face an error with links from the Wearables Developer Center because it uses a different domain ([developer.meta.com](https://developer.meta.com/)). | Logout from [developers.meta.com](https://developers.meta.com/) before signing up for the Wearables Developer Center. |

### Version Dependencies

## Overview
This page outlines the supported versions of the Meta AI App and glasses firmware for each release of the Meta Wearables Device Access Toolkit SDK.

## 0.4.0

| App/Firmware | Support |
| --- | --- |
| Meta AI App (Android) | V254 |
| Meta AI App (iOS) | V254 |
| Ray-Ban Meta glasses | V20 |
| Meta Ray-Ban Display glasses | V21 |

## 0.3.0

| App/Firmware | Support |
| --- | --- |
| Meta AI App (Android) | V249 |
| Meta AI App (iOS) | V249 |
| Ray-Ban Meta glasses | V20 |

## Section 2: API Reference

### DecoderError (enum)
Errors that can occur during media decoding operations.

### PhotoCaptureFormat (enum)
Supported formats for capturing photos from Meta Wearables devices.

### StreamSession

A class for managing media streaming sessions with Meta Wearables devices. Handles video streamin...

- start() → Starts video streaming from the device.
- stop() → Stops video streaming and releases all resources.
- capturePhoto(...) → Captures a still photo during streaming.
- streamSessionConfig (property) → The configuration used for this streaming session.
- state (property) → The current state of the streaming session.
- statePublisher (property) → Publisher for streaming session state changes.
- videoFramePublisher (property) → Publisher for video frames received from the streaming session.
- photoDataPublisher (property) → Publisher for photo data captured during the streaming session.
- errorPublisher (property) → Publisher for errors that occur during the streaming session.

### StreamingResolution (enum)
Valid Live Streaming resolutions. We are using 9:16 aspect ratio.

### PhotoData

A photo captured from a Meta Wearables device.

- data (property) → The photo data in the specified format.
- format (property) → The format of the captured photo data.

### StreamSessionConfig

Configuration for a media streaming session with a Meta Wearables device. Defines video codec, re...

- videoCodec (property) → The video codec to use for streaming.
- resolution (property) → The resolution at which to stream video content.
- frameRate (property) → The target frame rate for the streaming session.

### StreamSessionError (enum)
Errors that can occur during streaming sessions.

### StreamSessionState (enum)
Represents the current state of a media streaming session with a Meta Wearables device.

### VideoCodec (enum)
Specifies the video codec to use for streaming.

### VideoFrame

Represents a single frame of video data from a Meta Wearables device. Contains the raw video samp...

- makeUIImage() → Converts the video frame to a UIImage for display or processing. This method handles the conversi...
- sampleBuffer (property) → Provides access to the underlying video sample buffer.

### VideoFrameSize

Represents the width and height of a video frame in pixels.

- width (property) → The width of the video frame in pixels.
- height (property) → The height of the video frame in pixels.

### Announcer

A protocol for objects that can announce events to registered listeners.

- listen(...) → Registers a listener for events of type T.

### AnyListenerToken

A token that can be used to cancel a listener subscription. When the token is no longer reference...

- cancel() → Cancels the listener subscription asynchronously.

### AutoDeviceSelector

A device selector that automatically selects the best available device. Selects the first connect...

- activeDeviceStream() → Creates a stream of active device changes that updates whenever the device list changes.
- activeDevice (property) → The currently active device identifier.

### Device

AI glasses accessible through the Wearables Device Access Toolkit.

- nameOrId() → Returns the device name if available, otherwise returns the device identifier. This provides a fa...
- addLinkStateListener(...) → Adds a listener to receive notifications when the device's link state changes.
- addCompatibilityListener(...) → Adds a listener to receive notifications when the device's compatibility changes.
- deviceType() → Returns the type of this device (e.g., Ray-Ban Meta).
- compatibility() → Returns true if the version of this device is compatible with the Wearables Device Access Toolkit.
- identifier (property) → The unique identifier for this device.
- name (property) → The human-readable device name, or empty string if unavailable.
- linkState (property) → The current connection state of the device.

### DeviceSelector

Protocol for selecting which device should be used for operations. Device selectors determine whi...

- activeDeviceStream() → Creates a stream of active device changes.
- activeDevice (property) → The currently active device identifier, if any.

### DeviceState

Represents the current state of a Meta Wearables device, including battery and hinge information.

- batteryLevel (property) → The current battery level of the device as a percentage (0-100).
- hingeState (property) → The current state of the device's hinge mechanism.

### DeviceStateSession

Manages a session for monitoring device state changes.

- start() → Starts the device state session.
- stop() → Stops the device state session.
- state (property) → The current state of the device session.

### DeviceType (enum)
Represents the types of Meta Wearables devices supported by the Wearables Device Access Toolkit.

### HingeState (enum)
Represents the physical state of the device's hinge mechanism.

### LinkState (enum)
Represents the connection state between a device and the Wearables Device Access Toolkit.

### Mutex

- withLock(...)

### Permission (enum)
Represents the types of permissions that can be requested from AI glasses.

### PermissionError (enum)
Errors that can occur during permission requests.

### PermissionStatus (enum)
Represents the status of a permission request.

### RegistrationError (enum)
Error conditions that can occur during the registration process.

### RegistrationState (enum)
Represents the current state of user registration with the Meta Wearables platform.

### SessionState (enum)
Represents the current state of a device session in the Wearables Device Access Toolkit.

### SpecificDeviceSelector

A device selector that always selects a specific, predetermined device. Use this when you want to...

- activeDeviceStream() → Creates a stream that immediately yields the specific device and then completes.
- activeDevice (property) → The currently active device identifier.

### UnregistrationError (enum)
Error conditions that can occur during the unregistration process.

### Wearables (enum)
The entry point for configuring and accessing the Wearables Device Access Toolkit.

### WearablesError (enum)
Errors that can occur during Device Access Toolkit configuration.

### WearablesHandleURLError (enum)
Errors that can occur during URL handling.

### WearablesInterface

The primary interface for Wearables Device Access Toolkit.

- addRegistrationStateListener(...) → Adds a listener to receive callbacks when the registration state changes. The listener is immedia...
- registrationStateStream() → Creates an <code>AsyncStream</code> for observing registration state changes.
- startRegistration() → Initiates the registration process with AI glasses.
- handleUrl(...) → Handles callback URLs from the Meta AI app during registration and permission flows.
- startUnregistration() → Initiates the unregistration process with AI glasses.
- addDevicesListener(...) → Adds a listener to receive callbacks when the device list changes. The listener is immediately ca...
- devicesStream() → Creates an <code>AsyncStream</code> for observing device list changes.
- deviceForIdentifier(...) → Fetch the underlying [Device](/reference/ios_swift/dat/0.4/mwdatcore_device) object for a given [...
- checkPermissionStatus(...) → Checks if a specific permission is granted for the current application.
- requestPermission(...) → Requests a specific permission on AI glasses.
- addDeviceSessionStateListener(...) → Adds a listener to receive callbacks when the session state changes for a specific device. The li...
- registrationState (property) → The current registration state of the user's devices. See [RegistrationState](/reference/ios_swif...
- devices (property) → The current list of devices available.

### MockDevice

- powerOn() → Powers on the mock device.
- powerOff() → Powers off the mock device.
- don() → Simulates putting on (donning) the device.
- doff() → Simulates taking off (doffing) the device.
- deviceIdentifier (property) → The unique device identifier for this mock device.

### MockCameraKit

A suite for mocking camera functionality.

- setCameraFeed(...) → Sets the camera feed from a video file.
- setCapturedImage(...) → Sets the captured image from an image file.

### MockDeviceKit (enum)
The entry-point to the MockDeviceKit for managing simulated Meta Wearables devices. Use this in t...

### MockDeviceKitError (enum)
Errors that can occur when using MockDeviceKit.

### MockDeviceKitInterface

Interface for managing mock Meta Wearables devices for testing and development.

- pairRaybanMeta() → Pairs a simulated Ray-Ban Meta device.
- unpairDevice(...) → Unpairs a simulated device.
- pairedDevices (property) → The list of all currently paired mock devices.

### MockDisplaylessGlasses

Protocol for simulating displayless smart glasses behavior in testing and development. Provides f...

- fold() → Simulates folding the glasses into a closed position.
- unfold() → Simulates unfolding the glasses into an open position.
- getCameraKit() → Gets the suite for mocking camera functionality.

### MockRaybanMeta

Protocol for simulating Ray-Ban Meta smart glasses behavior in testing and development. Inherits ...


