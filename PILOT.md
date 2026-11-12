# Quiet iOS simulator pilot

This is a personal, unofficial fork of Telegram iOS. The first milestone checks
whether a standard GitHub-hosted ARM64 macOS runner can build the app and keep a
fresh, logged-out simulator process alive long enough to capture its screen.

The app is not yet a replacement for the owner's Windows fork. Personal UI
changes and strict mute have not been ported. This workflow does not upload
anything to TestFlight.

## Reviewed inputs

- Upstream: TelegramMessenger/Telegram-iOS, tag release-13.2, peeled commit
  24638bc69bd083981b178dd1e7f921eebbf7fc46.
- Required Xcode: 27.1 and iOS Simulator SDK 27.1, checked without overrides.
- Runner: GitHub-hosted xcode-27, ARM64. The preview image can change; the
  report records the actual image and installed Xcodes.
- Actions are pinned to reviewed release commits.
- The tgcalls submodule uses its absolute upstream URL so cloning this fork
  does not incorrectly resolve it as mennya/tgcalls.

## Run

Start **iOS Simulator Pilot** manually in GitHub Actions. There are no push or
scheduled triggers. The job uses a standard runner in this public repository.
Do not switch to a paid runner or change spending limits as an automatic retry.
An actionlint check runs on Linux before the Mac job. Its binary is pinned by
version and SHA256. The lint configuration recognizes the documented xcode-27
preview label; the simulator runtime guard still rejects self-hosted runners.

The preflight checks the selected compiler, architecture, RAM and disk. Only on
this ephemeral GitHub runner, it may remove unselected Xcode app bundles to make
space; it preserves /Applications/Xcode_27.1.app. It refuses a different host,
repository or runner type before touching anything.

The build uses the upstream Make.py build path with debug_sim_arm64.
The xcodeManagedCodesigning option selects the upstream no-op profile source for
this experiment: no Apple account, certificates or private signing repository is
loaded. A reviewed Bazelrc overlay disables provisioning profiles and limits
parallelism to two actions with a 4500 MB memory budget. The report records the
original, overlay and effective Bazelrc digests.

The first native build succeeded in run 43202755565 (8186 actions). Its fresh
simulator install exceeded the initial 120-second deadline, before app launch.
For a startup-only retry, enable **reuse_verified_pilot**. This downloads only
that fixed run's artifact and verifies every retained checksum plus the pinned
IPA SHA256 6d13c409d5c37a95d5d37cddd7d1fda0cd6420a189193c9fe05e9c24b7041d7c.
It preserves compiled source f3fe3730efe42bcaefc5deca551a372a94154f3c and build
100002, and records the newer test harness separately. It never substitutes an
arbitrary artifact. This option depends on the original 14-day artifact
retention; leave it disabled for a new native build.

Startup selects an installed compatible iPhone/runtime pair at or below SDK 27.1
from the stock simulator inventory, then creates a fresh owned device. It never
boots an existing device. Some installed runtime images support only specific
models; a global device-type list does not establish compatibility. The report
records the selected model and runtime. Boot and install have bounded 300-second
and 600-second deadlines. The job records command timings,
retains a short filtered installer log on failure, and bounds cleanup of its own
simulator. The whole startup step is limited to 25 minutes.

The configuration contains deliberately unusable placeholder Telegram API
credentials. Do not attempt an account login. Real Telegram credentials and
Apple signing material belong in protected secret storage for a later reviewed
device/TestFlight workflow.

## Evidence and limits

The workflow retains a simulator-only archive, filtered build log, report,
checksums and, on successful process launch, welcome.png. It validates the
bundle identifier and Mach-O simulator platform before installation. It creates
and deletes only its own simulator and imports no sessions.

Inspect the screenshot separately. A successful compiler, living process or
captured screenshot is not proof of a rendered welcome screen, authenticated
messaging, push notifications, calls, strict mute or TestFlight installation.
The archive cannot be installed as an iPhone release.

The working Mac and MS01 services are outside this experiment. MS01 is used only
for the source worktree and retained reports.

After this build path is validated, the next milestone needs the owner's Apple
Developer Program membership, own signing/provisioning material and APNs
configuration for the Telegram API application, followed by internal TestFlight
installation and ordinary owner login on the iPhone.
