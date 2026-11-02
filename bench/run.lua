-- Benchmark sweep from 1 to 10 pickups, with bench/sink.py running as the endpoint:
--   extensions.load('BNGLink_bench_run'); extensions.BNGLink_bench_run.start()
-- Writes bench/captures/nXX/game.json. The sink writes the init, sync and summary files next to it.

local M = {}

local logTag = 'BNGLinkBench'
local WARMUP_S = 2
local RECORD_S = 5
local VEHICLE_MIN = 1
local VEHICLE_MAX = 10
local READY_TIMEOUT_S = 30
local INIT_TIMEOUT_PER_VEHICLE_S = 90
local INPUT_INTERVAL_S = 0.25
local CAPTURE_DIR = 'lua/ge/extensions/BNGLink/bench/captures'

local BNGLink
local Realtime
local state = nil

local function setPhase(name, timeout)
  state.phase = name
  state.phaseStarted = os.clockhp()
  state.timeout = timeout
end

local function phaseTimedOut()
  return os.clockhp() - state.phaseStarted > state.timeout
end

local function forVehicles(fn)
  for i = 0, be:getObjectCount() - 1 do
    fn(be:getObject(i), i)
  end
end

local function allVehiclesReady(target)
  if be:getObjectCount() ~= target then
    return false
  end
  for i = 0, target - 1 do
    if not be:getObject(i):isReady() then
      return false
    end
  end
  return true
end

-- Far apart so the vehicles never interact
local function benchPosition(index)
  return vec3(300 * index, 300 * index, 1)
end

local function placeVehicles()
  forVehicles(function(veh, index)
    veh:setPosition(benchPosition(index))
  end)
end

local function setInput(throttle)
  local command = string.format("input.event('parkingbrake', 0, 1); input.event('throttle', %.3f, 1)", throttle)
  forVehicles(function(veh)
    veh:queueLuaCommand(command)
  end)
end

local function neutralizeInput()
  forVehicles(function(veh)
    veh:queueLuaCommand("input.event('throttle', 0, 1); input.neutralizeControls()")
  end)
end

local function writeGameResult()
  local dir = string.format('%s/n%02d', CAPTURE_DIR, state.target)
  FS:directoryCreate(dir, true)

  local bench = Realtime.readBench()
  local result = {
    vehicles = state.target,
    warmup_s = WARMUP_S,
    record_s = RECORD_S,
    game_fps = state.samples > 0 and state.fpsSum / state.samples or 0,
    fps_samples = state.samples,
    send_frames = bench.frames,
    send_us_frame = bench.sendUsFrame,
    send_bytes_frame = bench.bytesFrame,
    update_frames = bench.updateFrames,
    update_us_frame = bench.updateUsFrame,
  }
  jsonWriteFile(dir .. '/game.json', result, true)
end

local function fail(message)
  log('E', logTag, message)
  BNGLink.stopRealtime()
  Realtime.disableBench()
  neutralizeInput()
  state = nil
end

local function startTarget()
  local target = state.target
  state.fpsSum = 0
  state.samples = 0
  state.inputTimer = 0
  log('I', logTag, string.format('starting %d vehicle benchmark', target))

  if target == 1 then
    core_vehicles.removeAllExceptCurrent()
    if not core_vehicles.replaceVehicle('pickup') then
      fail('failed to replace vehicle with pickup')
      return
    end
  else
    local veh = core_vehicles.spawnNewVehicle('pickup', {
      pos = benchPosition(target - 1),
      autoEnterVehicle = false,
    })
    if not veh then
      fail('failed to spawn pickup')
      return
    end
  end

  setPhase('waitReady', READY_TIMEOUT_S)
end

local function startInit()
  BNGLink.stopRealtime()
  neutralizeInput()
  BNGLink.initContent()
  setPhase('waitInit', INIT_TIMEOUT_PER_VEHICLE_S * state.target)
end

local function startRealtime()
  Realtime.resetBench()
  BNGLink.startRealtime()
  setInput(0.18)
  state.recordStarted = os.clockhp()
  state.benchWindowStarted = false
  setPhase('record', WARMUP_S + RECORD_S + 5)
end

local function finishTarget()
  BNGLink.stopRealtime()
  neutralizeInput()
  writeGameResult()
  Realtime.disableBench()
  log('I', logTag, string.format('finished %d vehicle benchmark', state.target))

  state.target = state.target + 1
  if state.target > VEHICLE_MAX then
    log('I', logTag, 'benchmark sweep complete')
    state = nil
    return
  end
  startTarget()
end

local function updateWaitReady()
  if phaseTimedOut() then
    fail(string.format('timed out waiting for %d ready vehicles', state.target))
    return
  end
  if not allVehiclesReady(state.target) then
    return
  end
  placeVehicles()
  startInit()
end

local function updateWaitInit()
  if phaseTimedOut() then
    fail(string.format('timed out waiting for BNGLink init at %d vehicles', state.target))
    return
  end
  if not BNGLink.isInitActive() then
    startRealtime()
  end
end

local function updateRecord(dt)
  if phaseTimedOut() then
    fail(string.format('timed out recording %d vehicles', state.target))
    return
  end

  state.inputTimer = state.inputTimer - dt
  if state.inputTimer <= 0 then
    state.inputTimer = INPUT_INTERVAL_S
    setInput(0.18)
  end

  local elapsed = os.clockhp() - state.recordStarted
  if elapsed >= WARMUP_S and not state.benchWindowStarted then
    state.benchWindowStarted = true
    Realtime.resetBench()
  end
  if elapsed >= WARMUP_S and elapsed < WARMUP_S + RECORD_S then
    state.fpsSum = state.fpsSum + (dt > 0 and 1 / dt or 0)
    state.samples = state.samples + 1
  elseif elapsed >= WARMUP_S + RECORD_S then
    finishTarget()
  end
end

local function start()
  if state then
    log('W', logTag, 'benchmark is already running')
    return
  end

  extensions.load('BNGLink_BNGLink')
  BNGLink = extensions.BNGLink_BNGLink
  Realtime = extensions.BNGLink_Realtime

  state = { target = VEHICLE_MIN }
  startTarget()
end

local function stop()
  if not state then
    return
  end
  BNGLink.stopRealtime()
  Realtime.disableBench()
  neutralizeInput()
  state = nil
  log('I', logTag, 'benchmark stopped')
end

local function onUpdate(dt)
  if not state then
    return
  end
  if state.phase == 'waitReady' then
    updateWaitReady()
  elseif state.phase == 'waitInit' then
    updateWaitInit()
  elseif state.phase == 'record' then
    updateRecord(dt)
  end
end

local function onExtensionUnloaded()
  stop()
end

M.start = start
M.stop = stop
M.onUpdate = onUpdate
M.onExtensionUnloaded = onExtensionUnloaded

return M
