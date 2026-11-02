-- Realtime stream: per-frame node positions + prop transforms of every ready vehicle.
-- This is the sender hot path; it only reads the caches built at init.

local M = {}

local ffi = require('ffi')
local uintArray = ffi.typeof('uint32_t[?]')

local logTag = 'BNGLink'

local STATS_LOG_FRAMES = 240

local FFIHelpers

local syncSeq = 0
local packetHeader = ffi.new('struct { uint32_t seq, vehicleCount; double simTime; }')
local vehicleHeader = ffi.new('unsigned int[2]')
local propHeader = ffi.new('unsigned int[1]')
local origin = ffi.new('float[3]')
local nodeBuf = {size = 0, data = ffi.new('float[?]', 0)}
local propBuf = {size = 0, data = ffi.new('float[?]', 0)}

-- stats is logged every STATS_LOG_FRAMES (enableStats), bench accumulates until readBench (bench/run.lua)
local stats = nil
local bench = nil

local function newCounters()
  return {frames = 0, sendUs = 0, maxUs = 0, bytes = 0, updateFrames = 0, updateUs = 0, gcBase = collectgarbage('count')}
end

local function enableStats()
  stats = newCounters()
end

local function resetBench()
  bench = newCounters()
end

local function disableBench()
  bench = nil
end

local function statsEnabled()
  return stats ~= nil or bench ~= nil
end

local function addSend(counters, us, byteCount)
  counters.frames = counters.frames + 1
  counters.sendUs = counters.sendUs + us
  counters.maxUs = math.max(counters.maxUs, us)
  counters.bytes = counters.bytes + byteCount
end

local function noteSend(elapsed, byteCount)
  local us = elapsed * 1e6
  if bench then
    addSend(bench, us, byteCount)
  end
  if stats then
    local s = stats
    addSend(s, us, byteCount)
    if s.frames >= STATS_LOG_FRAMES then
      log('I', logTag, string.format(
          'STATS send: %.2f us/frame avg (max %.2f), GC delta %.1f B/frame, %d bytes/frame',
          s.sendUs / s.frames, s.maxUs,
          (collectgarbage('count') - s.gcBase) * 1024 / s.frames, s.bytes / s.frames))
      s.frames, s.sendUs, s.maxUs, s.bytes = 0, 0, 0, 0
      s.gcBase = collectgarbage('count')
    end
  end
end

local function noteUpdate(elapsed)
  local us = elapsed * 1e6
  if bench then
    bench.updateFrames = bench.updateFrames + 1
    bench.updateUs = bench.updateUs + us
  end
  if stats then
    local s = stats
    s.updateFrames = s.updateFrames + 1
    s.updateUs = s.updateUs + us
    if s.updateFrames >= STATS_LOG_FRAMES then
      log('I', logTag, string.format('STATS onUpdate: %.2f us/frame avg', s.updateUs / s.updateFrames))
      s.updateFrames, s.updateUs = 0, 0
    end
  end
end

local function readBench()
  return {
    frames = bench.frames,
    sendUsFrame = bench.frames > 0 and bench.sendUs / bench.frames or 0,
    bytesFrame = bench.frames > 0 and bench.bytes / bench.frames or 0,
    updateFrames = bench.updateFrames,
    updateUsFrame = bench.updateFrames > 0 and bench.updateUs / bench.updateFrames or 0,
  }
end

-- Block headers stay constant until the next reinit, so they are encoded once per init.
local function prepareVehicle(vehicleId, state)
  vehicleHeader[0] = vehicleId
  vehicleHeader[1] = #state.cachedNodeIds
  state.syncVehicleHdr = ffi.string(vehicleHeader, 8)
  propHeader[0] = #state.cachedProps
  state.syncPropHdr = ffi.string(propHeader, 4)
  state.damageRevision = 0
  state.syncDamage = ffi.string(uintArray(4), 16)
end

local function updateDamage(state, brokenBeams, hiddenProps, switches)
  local count = 4 + #brokenBeams * 2 + #hiddenProps + #switches * 2
  local data = uintArray(count)
  state.damageRevision = state.damageRevision + 1
  data[0] = state.damageRevision
  data[1] = #brokenBeams
  local offset = 2
  for _, beam in ipairs(brokenBeams) do
    data[offset], data[offset + 1] = beam[1], beam[2]
    offset = offset + 2
  end
  data[offset] = #hiddenProps
  offset = offset + 1
  for _, pid in ipairs(hiddenProps) do
    data[offset] = pid
    offset = offset + 1
  end
  data[offset] = #switches
  offset = offset + 1
  for _, switch in ipairs(switches) do
    data[offset], data[offset + 1] = switch.id, switch.materialId
    offset = offset + 2
  end
  state.syncDamage = ffi.string(data, count * 4)
end

local function growBuffer(buffer, count)
  if count > buffer.size then
    buffer.data = ffi.new('float[?]', count)
    buffer.size = count
  end
  return buffer.data
end

local function buildVehicleSyncBlock(veh, state)
  local nodeIds = state.cachedNodeIds
  local nodeFloats = #nodeIds * 3
  local nodes = growBuffer(nodeBuf, nodeFloats)
  for i = 1, #nodeIds do
    local base = (i - 1) * 3
    nodes[base], nodes[base + 1], nodes[base + 2] = veh:getNodePositionXYZ(nodeIds[i])
  end

  origin[0], origin[1], origin[2] = veh:getPositionXYZ()

  local cachedProps = state.cachedProps
  local propFloats = #cachedProps * 12
  local props = growBuffer(propBuf, propFloats)
  local writeMatrix12 = FFIHelpers.writeMatrix12
  for i = 1, #cachedProps do
    writeMatrix12(props, (i - 1) * 12, cachedProps[i].propObj:getLiveTransformWorld())
  end

  return state.syncVehicleHdr
      .. ffi.string(nodes, nodeFloats * 4)
      .. ffi.string(origin, 12)
      .. state.syncPropHdr
      .. ffi.string(props, propFloats * 4)
      .. state.syncDamage
end

local function sendFrame(vehicleStates, simTime)
  local t0 = statsEnabled() and os.clockhp()

  local blocks = {}
  for i = 0, be:getObjectCount() - 1 do
    local veh = be:getObject(i)
    local state = vehicleStates[veh:getId()]
    if state and state.contentReady then
      blocks[#blocks + 1] = buildVehicleSyncBlock(veh, state)
    end
  end
  if #blocks == 0 then
    return
  end

  syncSeq = syncSeq + 1
  packetHeader.seq = syncSeq
  packetHeader.vehicleCount = #blocks
  packetHeader.simTime = simTime
  local payload = ffi.string(packetHeader, 16) .. table.concat(blocks)
  if t0 then
    noteSend(os.clockhp() - t0, #payload)
  end
  extensions.BNGLink_UDPComm.sendStream(payload)
end

local function onExtensionLoaded()
  FFIHelpers = extensions.BNGLink_FFIHelpers
end

M.onExtensionLoaded = onExtensionLoaded
M.prepareVehicle = prepareVehicle
M.updateDamage = updateDamage
M.sendFrame = sendFrame
M.enableStats = enableStats
M.statsEnabled = statsEnabled
M.noteUpdate = noteUpdate
M.resetBench = resetBench
M.disableBench = disableBench
M.readBench = readBench

return M
