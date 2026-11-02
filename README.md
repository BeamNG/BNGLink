# BNGLink

BNGLink mirrors the vehicles of a running BeamNG.tech session into **Blender** or **Unreal Engine** in real time, and records them as **USD takes** that you can load later.

It is built for cinema and VFX work: drive, crash or stage a scene in BeamNG, and watch the same vehicles move, deform, and break apart in your 3D software's viewport. You can also record the scene and bring it back as keyframed animation (Blender) or a Level Sequence (Unreal).

> [!IMPORTANT]
> BNGLink requires a valid BeamNG.tech license. Companies, studios and other professional users can contact [licensing@beamng.gmbh](mailto:licensing@beamng.gmbh) about our professional licensing options.

- [How it works](#how-it-works)
- [Installation](#installation)
- [Usage](#usage)
- [Compatibility](#compatibility)
- [Known limitations](#known-limitations)
- [Troubleshooting](#troubleshooting)
- [Contributing](#contributing)
- [Technical documentation](#technical-documentation)

## How it works

BNGLink has three parts. Install the ones you need.

| Part | Installed in | Role |
| --- | --- | --- |
| BNGLink mod | BeamNG.tech | Sends the vehicles. Adds a **BNGLink** tab to the pause menu. |
| BNGLink add-on | Blender | Receives the vehicles and builds them in the scene. Records and loads takes. |
| BngLinkBridge plugin | Unreal Engine | Same as the Blender add-on, for Unreal. |

When you start the link, BeamNG sends each vehicle once: its 3D meshes, materials, textures and paints. This can take a few seconds per vehicle. Meshes and textures already sent during the session are not sent again.
Then, BeamNG streams the movement and deformation of every vehicle on every frame.

Changes made in BeamNG while the link runs are followed automatically:

- driving, deformation and crash damage, including broken glass and parts falling off
- lights, turn signals and other glowing materials
- paint colors, skins and paint designs (decals)
- license plates and digital dashboard screens
- part changes in the vehicle configuration
- seat occupants, when they are enabled in the BeamNG settings

The BeamNG computer and the Blender or Unreal computer can be the same machine or two machines on the same network.
**Tip:** *For best results, avoid using USB-to-Ethernet adapters when connecting two computers on the same network. They can cause connection issues and may not be reliable enough for real-time syncing.*

## Installation

Get the three zip files of the latest BNGLink release:

- `bnglink_beamng.zip`: the BeamNG mod
- `bnglink_blender.zip`: the Blender add-on
- `bnglink_unreal.zip`: the Unreal plugin

Always use the three files from the same release. Parts from different releases may not understand each other.

### BeamNG.tech

1. Copy `bnglink_beamng.zip` into the `mods` folder of your BeamNG user folder. Default for Windows is:
   ```
     %LOCALAPPDATA%\BeamNG\BeamNG.tech\current\mods
   ```

     or on Linux:

     Paste the path into the file explorer address bar to open it. Do not unzip the file.
2. Start BeamNG.tech. **BNGLink** appears in the mod manager and is enabled automatically.
3. Load a level, press **Esc** and check that the pause menu has a **BNGLink** tab.

### Blender

#### 1. Install Numba

The add-on uses the Numba Python package to deform the vehicles fast enough. Blender does not include it, so you need to install it once into Blender's own Python.

1. Close Blender if it is open.
2. Open a Command Prompt, PowerShell or Terminal window.
3. Run this command. Adjust the path to your Blender installation and version. Default for Windows is:
   ```
     "C:\Program Files\Blender Foundation\Blender 5.2\5.2\python\bin\python.exe" -m pip install numba
   ```

     or on Linux:

If the installation defaults to your generic python user folder, you can copy the following folders from your sites-packages folder to the lib folder in Blender's Python folder :

- numba
- llvmlite
- llvmlite.libs

To check the installation, start Blender, open the **Scripting** workspace and type `import numba` in the Python console. No error message means it worked.

#### 2. Install the add-on

1. Start Blender.
2. Open **Edit > Preferences > Add-ons**.
3. In the drop-down menu at the top right, choose **Install from Disk** and select `bnglink_blender.zip`.
4. Make sure **BNGLink** is enabled in the add-on list.
5. In the 3D viewport, press **N** to open the sidebar. A **BNGLink** tab is now available.

### Unreal Engine

#### 1. Install NumPy and Numba

The plugin uses the NumPy and Numba Python packages. Unreal does not include them, so you need to install them once into Unreal's Python.

1. Close Unreal Editor.
2. Open a Command Prompt, PowerShell or Terminal window.
3. Run this command. Adjust the path to your Unreal installation and version. Default for Windows is:
   ```
     "C:\Program Files\Epic Games\UE_5.8\Engine\Binaries\ThirdParty\Python3\Win64\python.exe" -s -m pip install numpy numba
   ```

#### 2. Install the plugin

1. Close Unreal Editor.
2. Open your project folder (the folder that contains your `.uproject` file) and create a `Plugins` folder in it if there is none.
3. Unzip `bnglink_unreal.zip` into the `Plugins` folder. You should end up with `<YourProject>/Plugins/BngLinkBridge/BngLinkBridge.uplugin`.
4. Open the project. Unreal reports that the **BngLinkBridge** module is missing and asks to rebuild it. Answer **Yes**.
5. After the editor starts, the **BNGLink** panel opens automatically.

> [!WARNING]
> **The plugin must be compiled.** BngLinkBridge is delivered as C++source code, and Unreal compiles it the first time you open the project. This needs **Visual Studio** with the **Game development with C++** workload installed. Check Epic's documentation for the Visual Studio version that matches your Unreal version.
>
> **Blueprint-only projects** (projects created without C++) sometimes cannot compile plugins and show an error like *"BngLinkBridge could not be compiled"* instead. In that case, convert the project to a C++ project: open it without the plugin (temporarily move the `BngLinkBridge` folder out of `Plugins`), choose **Tools > New C++ Class**, create an empty class, close the editor, put the plugin folder back and open the project again.

The plugin automatically enables the Unreal plugins it needs: Python Editor Script Plugin, Editor Scripting Utilities, Procedural Mesh Component and Sequencer Scripting.

To **record and load USD takes**, also enable the **USD Importer** plugin in **Edit > Plugins**, then restart the editor. Without it, loading takes is disabled and recording fails.

If you close the BNGLink panel, reopen it with **File > Execute Python Script** and select `<YourProject>/Plugins/BngLinkBridge/Content/Python/ui.py`.

## Usage

### Configuration

BeamNG and the receiver (Blender or Unreal) talk to each other over the network on two ports:

| Setting | Default | Meaning |
| --- | --- | --- |
| BeamNG port | `6000` | Port BeamNG listens on, for the receiver's replies |
| Receiver IP | `127.0.0.1` | Address of the computer running Blender or Unreal |
| Receiver port | `6001` | Port the receiver listens on, for the vehicles |

**Everything on one computer:** keep the defaults. Nothing to configure.

**BeamNG and the receiver on two computers:**

| Where | Field | Value |
| --- | --- | --- |
| BeamNG, BNGLink tab | Receiver IP | IP address of the Blender or Unreal computer, for example `192.168.1.20` |
| Blender or Unreal, BNGLink panel | BeamNG IP | IP address of the BeamNG computer, for example `192.168.1.10` |
| Blender or Unreal, BNGLink panel | Receiver IP | IP address of the Blender or Unreal computer, or `0.0.0.0` to listen on every network |

Also make sure that:

- The ports match on both sides.
- You use IP addresses, not computer names. The receiver ignores data that does not come from the exact BeamNG IP and port it is set to.
- The firewall allows incoming UDP traffic on port `6000` on the BeamNG computer, and on port `6001` on the receiver computer.

**Changing the defaults:** settings are not saved by BeamNG or by the Unreal panel. They return to their defaults on the next start. Blender saves them in the `.blend` file. To change the defaults everywhere, set these environment variables before starting the applications:

| Variable | Default | Used by |
| --- | --- | --- |
| `BNGLINK_HOST` | `127.0.0.1` | Default IP address on both sides |
| `BNGLINK_BEAMNG_PORT` | `6000` | BeamNG port on both sides |
| `BNGLINK_RECEIVER_PORT` | `6001` | Receiver port on both sides |
| `BNGLINK_TAKES_DIR` | `<your user folder>\BNGLink\takes` | Default folder for USD takes in Blender and Unreal |

### Realtime link

>[!IMPORTANT]
>It is always preferable to start the receiver in Blender or Unreal first, and then start the link from BeamNG.
>By starting the link from BeamNG before the receiver is started, you may run into issues with the link not being able to connect properly.

#### 1. Start the receiver in Blender or Unreal

- **Blender:** in the 3D viewport sidebar (**N**), open the **BNGLink** tab and click **Start receiver**.
- **Unreal:** in the **BNGLink** panel, click **Start receiver**.

Only one receiver can run on a computer at a time. The connection settings are locked while the receiver runs, stop it to change them.

#### 2. Start the link from BeamNG

1. Load a level and spawn your vehicles.
2. Press **Esc** and open the **BNGLink** tab.
3. Click **Start live sync**.

BeamNG sends the vehicles one by one. The button next to it shows **Resyncing...** in the meantime. The vehicles then appear in Blender or Unreal and follow BeamNG live. The status at the top of the tab shows **Connected to ...** as long as the receiver answers.

BNGLink places the first vehicle it receives at the origin of your Blender or Unreal scene. Other vehicles keep their position relative to it.

In your Blender/Unreal scene, you can move the BeamNG empty to move the scene origin.
**Do not move/rotate/edit the vehicles themselves, at the risk of breaking the link.**

#### The BNGLink tab in BeamNG

| Control                   | What it does                                                                                                                                        |
| ------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------- |
| Status                    | **Connected to** `<IP>:<port>` when the receiver answers, **Disconnected** otherwise.                                                               |
| Connection settings       | BeamNG port, receiver IP and receiver port. Changes apply when you leave the field or press Enter, and restart the link if it is running.           |
| Smooth digital dashboards | Updates digital dashboard screens 24 times per second instead of 4. **This can significantly reduce BeamNG's framerate.**                           |
| Vehicles                  | Every vehicle in the scene, with the one you drive highlighted. Turn a vehicle off to leave it out. The change applies on the next **Resync link**. |
| Start / Stop live sync    | Starts or stops streaming. When stopped, the vehicles stay in Blender or Unreal in their last position.                                             |
| Resync link               | Sends all vehicles again. Use it after spawning new vehicles, after changing the vehicle list, or when the receiver was restarted.                  |

#### Using the BeamNG console (headless mode)

When BeamNG runs without its user interface (for example when started with `-headless`, or driven by a script), the BNGLink tab is not available and the mod is not loaded automatically. You can control the link with Lua commands instead:

```lua
-- Load the mod (only needed when the BeamNG user interface is not running)
extensions.load('BNGLink_BNGLink')

-- Optional: change the connection settings
extensions.BNGLink_BNGLink.setConnectionSettings({beamngPort = 6000, receiverHost = '192.168.1.20', receiverPort = 6001})

-- Start, stop and resync the link
extensions.BNGLink_BNGLink.startRealtime()
extensions.BNGLink_BNGLink.stopRealtime()
extensions.BNGLink_BNGLink.resync()

-- Leave a vehicle out of the next resync (true to include it again)
extensions.BNGLink_BNGLink.setVehicleSyncEnabled(be:getPlayerVehicleID(0), false)

-- Smooth digital dashboards on or off
extensions.BNGLink_BNGLink.setSmoothDashboards(true)

-- Show the link status, connection settings and vehicle list
dump(extensions.BNGLink_BNGLink.getUiState())
```

`setConnectionSettings` returns `false` and keeps the current settings when a value is invalid.

### Record USD takes

A take is a single `.usdz` file containing the recorded vehicles, their animation, materials and textures.

1. Start the realtime link (see above).
2. In the **Record and replay** section of the Blender or Unreal panel, choose where to save the take:
   - a folder: each recording creates a new file named `bnglink_take_<date>_<time>.usdz`
   - or a file name ending in `.usdz`
3. Click **Start recording to file** (Blender) or **Start recording** (Unreal).
4. Drive, crash, stage your shot in BeamNG.
5. Click the same button again to stop. The take is saved at that moment.

Good to know:

- **Live preview:** turn it off while recording to stop updating the Blender or Unreal scene. The recording is not affected, and the receiver uses less processing power.
- **Recording follows BeamNG's clock**, not the real clock. Pausing BeamNG pauses the take. Slow motion is recorded at real speed with more samples per second, which is useful to capture fast events such as crashes in more detail.
- The framerate of the take is not fixed at recording. You choose it when you load the take.

### Load USD takes

The receiver does not need to run to load a take.

**Blender**

1. In the **Record and replay** section, set **Framerate** to the framerate you want, for example `24`.
2. Click **Load record from file** and select the `.usdz` file.

The vehicles are created in the scene with keyframes on every frame, including deformation, damage, paint, lights and texture changes. The scene framerate and frame range are set to match the take. Long takes take a while to load.

**Unreal**

1. Open a level and save it. The import saves the current level, so it fails on an unsaved new level.
2. In the **Record and replay** section, set **Playback FPS**.
3. Click **Load take** and select the `.usdz` file.

The vehicles are added to the current level as actors, and a Level Sequence is created under `/Game/BNGLink/Generated/Takes`. Open it in Sequencer to play, edit or render the take.

## Compatibility

| Software | Version |
| --- | --- |
| BeamNG.tech | Latest version |
| Blender | 5.2 or later |
| Unreal Engine | 5.8 or later |

BNGLink is developed and tested on Windows. Blender endpoint has been tested on Ubuntu as well. Other platforms and software versions may work but are not officially supported.

Use the same BNGLink release for all parts.

## Known limitations

- **Vehicles only.** The level, terrain, environment, camera, particles (smoke, sparks, dust) and sounds are not transferred.
- **New vehicles are not added automatically.** Click **Resync link** after spawning vehicles or loading another level. Removed vehicles disappear automatically.
- **Every vehicle is sent**, including traffic and parked vehicles. Many vehicles make the scene transfer longer and the link heavier. Turn off the ones you don't need in the vehicle list.
- **One receiver at a time.** BeamNG sends to one receiver, and only one receiver can run per computer and port.
- **Settings are not saved** in BeamNG and in the Unreal panel. See [Configuration](#configuration).
- **Blender textures live outside the** `.blend` **file.** They are stored in the add-on's `bng_textures` folder. Use **File > External Data > Pack Resources** before moving or sharing a `.blend` file. **Clear textures cache** deletes the textures of every vehicle that is not in the current scene, including those used by other saved `.blend` files.
- **T**he link uses unencrypted UDP without authentication. Use it on a trusted local network only.

## Troubleshooting

| Problem | Solution |
| --- | --- |
| BeamNG stays **Disconnected** | Start the receiver first, check the IP addresses and ports on both sides, and check the firewall. |
| Blender or Unreal reports *"Could not bind BNGLink receiver"* | Another receiver already uses the port. Stop it, or close the other Blender or Unreal instance. |
| A vehicle is missing in Blender or Unreal | Check that it is enabled in the BeamNG vehicle list, then click **Resync link**. |
| The scene looks out of date after restarting Blender or Unreal | Click **Resync link** in BeamNG. |
| Blender: *"No module named 'numba'"* in the system console | Numba is not installed in Blender's Python. See [Install Numba](#1-install-numba). |
| Unreal: the BNGLink panel does not open | Check the **Output Log** for errors. Usually NumPy or Numba is missing, or the plugin was not compiled. |
| Loading takes is greyed out | Blender: use an official Blender build, which includes USD support. Unreal: enable the **USD Importer** plugin. |

BeamNG logs BNGLink messages in `beamng.log` in the BeamNG user folder. Blender prints them in the system console (**Window > Toggle System Console**) and Unreal in the **Output Log**.

## Contributing

### Project layout

| Path | Content |
| --- | --- |
| `beamng/gelua/` | BeamNG sender, packed as `lua/ge/extensions/BNGLink/` and loaded as `BNGLink_*` game engine extensions. `BNGLink.lua` is the entry point (lifecycle, init queue, realtime policy). `UDPComm.lua` handles the transport, `InitGeometry.lua` and `InitMaterials.lua` build the vehicle init, `DynamicTextures.lua` and `MaterialSwitches.lua` follow runtime changes, `Realtime.lua` builds the per-frame stream, `FFIHelpers.lua` converts engine data to bytes. |
| `beamng/vlua/` | Vehicle extension, packed as `lua/vehicle/extensions/BNGLink.lua`. The sender loads it into each synced vehicle to collect broken beams, damaged materials and hidden props, and report them to `BNGLink.lua`. |
| `ui/` | BeamNG pause menu tab. Also loads the sender. |
| `bnglink/` | Shared receiver package: transport, decoder, asset cache, skinning kernels and `LinkSession`. |
| `bngusd/` | USD take recorder, writer and reader, plus the headless recorder `record.py`. |
| `blender/` | Blender add-on: `receiver.py` back end, `ui.py` sidebar panel, `import_take.py` take importer. |
| `ue5/` | Unreal endpoint: `main.py` back end, `ui.py` generated Editor Utility Widget, `import_take.py` take importer. |
| `ue5/UnrealPlugin/BngLinkBridge/` | Unreal C++ plugin: GPU-skinned body meshes, texture uploads and batched prop updates. |
| `packaging/` | Release builders. |
| `tests/` | Unit tests and the offline Lua sender harness. |
| `bench/` | Capture and replay benchmarks. |
| `docs/` | Protocol and USD take format references. |

### Building from source

Building needs Python 3 with Pillow (`pip install pillow`) for the BeamNG mod icons.

```
python packaging/build.py                    # all three zips into dist/
python packaging/build.py --target beamng    # or blender, ue5
python packaging/build.py --version 1.2.3
```

The version resolves as `--version` argument, then the latest `vX.Y.Z` git tag, then the root `VERSION` file. It is stamped into the BeamNG mod info, the Blender `bl_info` and the `.uplugin` descriptor. Never commit the `dist/` folder.

To release: bump `VERSION` (or tag `vX.Y.Z`), run `python packaging/build.py` and attach the three zips to the release.

### Development setup

Restart BeamNG, Blender or Unreal after editing loaded BNGLink modules.

For Blender, you have to remove the addon, then restart Blender and reinstall it for the changes to take effect.

### Guidelines

- The `blender/` and `ue5/` folders intentionally keep separate host-specific code for startup, vehicles, materials and textures. Shared protocol, transport, decoding and skinning code lives in `bnglink/`. One adapter never imports the other.
- Session caches live on `LinkSession` and `BngDecoder` and are cleared by `session_reset` or vehicle removal. The Blender and Unreal texture and mesh caches are local editor caches, not protocol state.
- Update `docs/` when you change the wire protocol or the take format.

### Benchmarks and statistics

1. Start `python bench/sink.py`, then in the BeamNG console run `extensions.load('BNGLink_bench_run'); extensions.BNGLink_bench_run.start()`. This records captures with 1 to 10 vehicles into `bench/captures/`.
2. `python bench/report.py` prints the transfer and game-side numbers.
3. `python bench/replay.py` measures decoding and skinning without an endpoint. `blender/bench.py` and `ue5/bench.py` replay the captures through the endpoints.
4. `python bench/usd_bench.py` measures USD record, save and read cost.

To log the sender cost (send time, garbage, bytes per frame) every 240 frames, run `extensions.BNGLink_Realtime.enableStats()` in the BeamNG console. To log the receiver throughput, set `DEBUG = True` in `blender/receiver.py` or `ue5/main.py`.

## Technical documentation

- [Protocol Description](docs/Protocol%20Description.md): the UDP wire protocol between BeamNG and the receivers.
- [USD Take Format](docs/USD%20Take%20Format.md): the layout of recorded `.usdz` takes.

## License

BNGLink is distributed under the terms of the [MIT License](LICENSE).
