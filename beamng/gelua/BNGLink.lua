-- Mirrors the vehicles to an external renderer (Blender, Unreal) over UDP:
-- init (bulk): geometry, skinning, props, materials, textures and paints, once per vehicle
-- sync (stream): node positions and prop transforms, every frame
-- Orchestrator: owns the vehicle states, init queue and realtime policy, and schedules the feature modules from the only onUpdate.

local M = {}

local logTag = 'BNGLink'

local LAYOUT_CHECK_INTERVAL = 2
local PAINT_CHECK_INTERVAL = 0.2
local PAINT_DESIGN_SETTLE_TIME = 1.5
local PLATE_TEXTURE_CHECK_INTERVAL = 0.25
local SMOOTH_CEF_TEXTURE_CHECK_INTERVAL = 1 / 24
local HEARTBEAT_INTERVAL = 1
local CONNECTION_TIMEOUT = 3

local SendResult = {
  Skip = 'skip',
  Retry = 'retry',
  Failed = 'failed',
  Sent = 'sent',
}

local VehiclePhase = {
  Idle = 'idle',
  Queued = 'queued',
  Preparing = 'preparing',
  AwaitingAck = 'awaiting_ack',
  Ready = 'ready',
  Failed = 'failed',
  Removed = 'removed',
}

local SUBMODULES = {
  'BNGLink_UDPComm',
  'BNGLink_FFIHelpers',
  'BNGLink_InitGeometry',
  'BNGLink_InitMaterials',
  'BNGLink_DynamicTextures',
  'BNGLink_MaterialSwitches',
  'BNGLink_Realtime',
}

local UDPComm
local InitGeometry
local InitMaterials
local DynamicTextures
local MaterialSwitches
local Realtime

local vehicleStates = {}
local disabledVehicleIds = {}
local initQueue = {}
local pendingVehicleChanges = {}
local pendingPaintUpdateVehicles = {}
local pendingPaintDesignVehicles = {}
local pendingInitVehicleId = nil
local initActive = false
local deliveredKeys = {}

local doRealtime = false
-- CEF dashboards are dumped through a blocking GPU readback + PNG encode, polling them often costs a lot of framerate
local smoothDashboards = false
-- Realtime was stopped by a vehicle change and restarts once the reinit completes
local resumeRealtimeAfterInit = false
local layoutCheckAccum = 0
local paintCheckAccum = 0
local paintDesignSettleTimer = 0
local plateTextureCheckAccum = 0
local cefTextureCheckAccum = 0
-- Accumulated onUpdate dtSim: the game's own clock, so takes pause and slow down with the simulation
local simClock = 0
local connectionClock = 0
local lastPingSent = -math.huge
local lastReceiverResponse = -math.huge

local function newVehicleState()
  return {
    contentReady = false,
    phase = VehiclePhase.Idle,
  }
end

local function getVehicleState(vehicleId)
  local state = vehicleStates[vehicleId]
  if not state then
    state = newVehicleState()
    vehicleStates[vehicleId] = state
  end
  return state
end

local function anyVehicleContentReady()
  for _, state in pairs(vehicleStates) do
    if state.contentReady then
      return true
    end
  end
  return false
end

local function initPending()
  return pendingInitVehicleId ~= nil or #initQueue > 0
end

local function isVehicleEnabled(vehicleId)
  return not disabledVehicleIds[vehicleId]
end

local function linkActive()
  return doRealtime or initActive
end

local function collectPendingKeys(geometryKey, textureList)
  local keys = {[geometryKey] = true}
  for _, entry in ipairs(textureList) do
    if not entry.dynamicKind then
      keys[entry.key] = true
    end
  end
  return keys
end

local function completeInitIfReady()
  if not initActive or initPending() then
    return
  end

  initActive = false
  UDPComm.sendControl('init_completed')
  Realtime.sendFrame(vehicleStates, simClock)

  if resumeRealtimeAfterInit then
    resumeRealtimeAfterInit = false
    doRealtime = true
    layoutCheckAccum = 0
    UDPComm.sendControl('realtime_started')
  end
end

local function sendInitContent(vehicleId)
  local veh = scenetree.findObjectById(vehicleId)
  if not veh then
    log('E', logTag, string.format('Could not prepare vehicle %d: vehicle not found', vehicleId))
    return SendResult.Skip
  end

  local state = getVehicleState(vehicleId)
  state.contentReady = false
  state.phase = VehiclePhase.Preparing

  if not MaterialSwitches.capture(vehicleId, veh, state) then
    return SendResult.Retry
  end
  log('I', logTag, string.format('Preparing vehicle %d for BNGLink', vehicleId))
  InitMaterials.invalidateDecalExport()
  InitMaterials.invalidatePlateTextures(vehicleId)

  local meshInfo = InitGeometry.readMeshInfo(vehicleId)
  if not meshInfo then
    return SendResult.Retry
  end
  local geometry = InitGeometry.build(veh, vehicleId, meshInfo, state.materialSwitchTargets)
  meshInfo:free()

  state.cachedLayoutSignature = geometry.layoutSignature
  state.cachedNodeIds = geometry.nodeIds
  state.cachedProps = geometry.props
  Realtime.prepareVehicle(vehicleId, state)

  local paints = InitMaterials.buildPaints(veh)
  local materials, textureList, skinName, materialIdByName = InitMaterials.build(
    veh, geometry.usedMaterialIds, state.materialData, deliveredKeys)
  if not state.materialSync then
    MaterialSwitches.setup(vehicleId, veh, state, materialIdByName)
  end
  state.materialIdByName = materialIdByName
  veh:queueLuaCommand(
    'if extensions.BNGLink then extensions.unload("BNGLink") end; extensions.load("BNGLink")'
  )

  local includeGeometry = not deliveredKeys[geometry.key]
  local geometryPayload = includeGeometry and geometry.payload or ''
  local payload, textures = InitMaterials.appendTextureBlob(geometryPayload, textureList, deliveredKeys)
  if #state.cachedNodeIds == 0 and #state.cachedProps == 0 then
    log('E', logTag, string.format('Could not prepare vehicle %d: no mesh data to send', vehicleId))
    return SendResult.Failed
  end

  local meta = {
    vehicleId = vehicleId,
    geometry = {
      key = geometry.key,
      included = includeGeometry,
      parts = includeGeometry and geometry.partsMeta or nil,
      props = includeGeometry and geometry.propsMeta or nil,
    },
    nodeIds = includeGeometry and state.cachedNodeIds or nil,
    props = geometry.propInstances,
    materials = materials,
    textures = textures,
    paints = paints,
  }
  if not UDPComm.sendBulk(payload, meta) then
    return SendResult.Retry
  end

  state.pendingKeys = collectPendingKeys(geometry.key, textureList)
  state.phase = VehiclePhase.AwaitingAck
  DynamicTextures.track(state, textureList)
  state.cachedPaints = paints
  if skinName ~= nil then
    state.cachedSkinName = skinName
  end
  MaterialSwitches.clearCapture(state)
  log(
    'I',
    logTag,
    string.format('Sent vehicle %d content to BNGLink (%d bytes, %d props)', vehicleId, #payload, #state.cachedProps)
  )
  return SendResult.Sent
end

-- One vehicle in flight at a time, the next one starts after the receiver confirms (onUdpReceive)
local function processInitQueue()
  if pendingInitVehicleId ~= nil then
    return
  end

  while #initQueue > 0 do
    local vehicleId = table.remove(initQueue, 1)
    pendingInitVehicleId = vehicleId
    local result = sendInitContent(vehicleId)
    if result == SendResult.Sent then
      return
    end
    pendingInitVehicleId = nil
    if result == SendResult.Retry then
      table.insert(initQueue, 1, vehicleId)
      return
    elseif result == SendResult.Failed then
      vehicleStates[vehicleId].phase = VehiclePhase.Failed
    elseif result == SendResult.Skip then
      local state = vehicleStates[vehicleId]
      if state then
        state.phase = VehiclePhase.Removed
      end
    end
  end

  completeInitIfReady()
end

local function queueVehicleInit(vehicleId)
  local state = getVehicleState(vehicleId)
  state.materialSync = nil
  state.contentReady = false
  state.phase = VehiclePhase.Queued
  MaterialSwitches.clearCapture(state)
  table.insert(initQueue, vehicleId)
  if not initActive then
    UDPComm.sendControl('init_start')
  end
  initActive = true
end

local function announceVehicleChange(vehicleId)
  pendingPaintUpdateVehicles[vehicleId] = nil
  pendingPaintDesignVehicles[vehicleId] = nil
  if doRealtime then
    doRealtime = false
    resumeRealtimeAfterInit = true
    UDPComm.sendControl('realtime_stopped')
  end
  UDPComm.sendControl('vehicle_change_detected')
end

-- The vehicle is being rebuilt by the game, processPendingVehicleChanges reinits it once it is ready
local function beginVehicleChange(vehicleId)
  local state = vehicleStates[vehicleId]
  if not state or not state.contentReady then
    return
  end

  state.contentReady = false
  pendingVehicleChanges[vehicleId] = true
  announceVehicleChange(vehicleId)
end

local function queueVehicleReinit(vehicleId)
  announceVehicleChange(vehicleId)
  queueVehicleInit(vehicleId)
end

local function sendPaintUpdate(vehicleId)
  local state = vehicleStates[vehicleId]
  if not (state and state.contentReady) then
    return SendResult.Skip
  end

  local paints = InitMaterials.buildPaints(scenetree.findObjectById(vehicleId))
  if not UDPComm.sendBulk('', {type = 'paint_update', vehicleId = vehicleId, paints = paints}) then
    return SendResult.Retry
  end

  state.cachedPaints = paints
  log('I', logTag, string.format('Sent vehicle %d paint update to BNGLink', vehicleId))
  return SendResult.Sent
end

local function checkPaintsAndQueueUpdates()
  for vehicleId, state in pairs(vehicleStates) do
    if state.contentReady and not pendingPaintUpdateVehicles[vehicleId]
        and InitMaterials.paintsChanged(InitMaterials.buildPaints(scenetree.findObjectById(vehicleId)), state.cachedPaints) then
      pendingPaintUpdateVehicles[vehicleId] = true
    end
  end
end

local function checkSkinsAndReinit()
  for vehicleId, state in pairs(vehicleStates) do
    if state.contentReady then
      local skinName = InitMaterials.getActiveSkinName(vehicleId)
      if skinName and skinName ~= (state.cachedSkinName or '') then
        log('I', logTag, string.format('Vehicle %d skin changed, updating BNGLink scene', vehicleId))
        state.cachedSkinName = skinName
        queueVehicleReinit(vehicleId)
        return true
      end
    end
  end
  return false
end

-- One paint update per frame, the bulk channel carries one transfer at a time
local function processPendingPaintUpdates()
  local vehicleId = next(pendingPaintUpdateVehicles)
  if vehicleId and sendPaintUpdate(vehicleId) ~= SendResult.Retry then
    pendingPaintUpdateVehicles[vehicleId] = nil
  end
end

-- Decal edits come in bursts, wait until they settle before exporting the decal textures
local function processPendingPaintDesignChanges(dt)
  if not next(pendingPaintDesignVehicles) then
    return
  end

  paintDesignSettleTimer = paintDesignSettleTimer + dt
  if paintDesignSettleTimer < PAINT_DESIGN_SETTLE_TIME then
    return
  end

  for vehicleId in pairs(pendingPaintDesignVehicles) do
    local result = DynamicTextures.sendUpdate(vehicleId, vehicleStates[vehicleId], 'decal')
    if result == DynamicTextures.Result.Retry then
      return
    end
    pendingPaintDesignVehicles[vehicleId] = nil
    if result == DynamicTextures.Result.None then
      queueVehicleReinit(vehicleId)
      return
    end
  end
  paintDesignSettleTimer = 0
end

local function processPendingVehicleChanges()
  local hasPending = false
  for vehicleId in pairs(pendingVehicleChanges) do
    hasPending = true
    if scenetree.findObjectById(vehicleId):isReady() then
      local meshInfo = InitGeometry.readMeshInfo(vehicleId)
      if meshInfo then
        meshInfo:free()
        pendingVehicleChanges[vehicleId] = nil
        queueVehicleInit(vehicleId)
      end
    end
  end
  return hasPending
end

local function reinitIfLayoutChanged(vehicleId)
  local state = vehicleStates[vehicleId]
  local veh = scenetree.findObjectById(vehicleId)
  if not (state and state.contentReady and veh
      and InitGeometry.layoutSignature(veh) ~= state.cachedLayoutSignature) then return false end

  log('I', logTag, string.format('Vehicle %d mesh changed, updating BNGLink scene', vehicleId))
  queueVehicleReinit(vehicleId)
  return true
end

local function checkLayoutsAndReinit()
  for vehicleId in pairs(vehicleStates) do
    if reinitIfLayoutChanged(vehicleId) then return true end
  end
  return false
end

local function onSettingsChanged()
  for vehicleId in pairs(vehicleStates) do
    reinitIfLayoutChanged(vehicleId)
  end
end

local function onSeatOccupantChanged(_, vehicleId)
  reinitIfLayoutChanged(vehicleId)
end

local function resetState()
  UDPComm.cancelBulk()
  vehicleStates = {}
  initQueue = {}
  pendingVehicleChanges = {}
  pendingPaintUpdateVehicles = {}
  pendingPaintDesignVehicles = {}
  pendingInitVehicleId = nil
  deliveredKeys = {}
  paintCheckAccum = 0
  paintDesignSettleTimer = 0
  plateTextureCheckAccum = 0
  cefTextureCheckAccum = 0
  DynamicTextures.reset()
  InitMaterials.resetTextureCaches()
end

local function onPaintDesignChanged()
  local vehicleId = be:getPlayerVehicleID(0)
  local state = vehicleStates[vehicleId]
  if state and state.contentReady then
    pendingPaintDesignVehicles[vehicleId] = true
    paintDesignSettleTimer = 0
  end
end

-- The receiver answers 'ping' with 'pong', and every init with 'init vehicle <id> ok' or 'init vehicle <id> missing'
local function onUdpReceive(payload)
  lastReceiverResponse = connectionClock
  if payload == 'pong' then
    return
  end

  local vehicleId, status = payload:match('^init vehicle (%d+) (%a+)$')
  vehicleId = tonumber(vehicleId)
  if not vehicleId or vehicleId ~= pendingInitVehicleId then
    return
  end

  if status == 'missing' then
    log('W', logTag, string.format('BNGLink receiver missed cached data for vehicle %d, resending full init', vehicleId))
    deliveredKeys = {}
    pendingInitVehicleId = nil
    table.insert(initQueue, 1, vehicleId)
    initActive = true
  elseif status == 'ok' then
    log('I', logTag, string.format('BNGLink confirmed vehicle %d is ready', vehicleId))
    local state = getVehicleState(vehicleId)
    state.contentReady = true
    state.phase = VehiclePhase.Ready
    for key in pairs(state.pendingKeys) do
      deliveredKeys[key] = true
    end
    state.pendingKeys = nil
    pendingInitVehicleId = nil
    completeInitIfReady()
  elseif status == 'failed' then
    log('W', logTag, string.format('BNGLink receiver failed to build vehicle %d, skipping it', vehicleId))
    local state = vehicleStates[vehicleId]
    state.contentReady = false
    state.pendingKeys = nil
    state.phase = VehiclePhase.Failed
    pendingInitVehicleId = nil
    completeInitIfReady()
  else
    log('W', logTag, string.format('Unknown BNGLink init reply for vehicle %d: %s', vehicleId, status))
    pendingInitVehicleId = nil
    table.insert(initQueue, 1, vehicleId)
    initActive = true
  end
end

local function onVehicleColorChanged(vehicleId)
  local state = vehicleStates[vehicleId]
  if state and state.contentReady then
    pendingPaintUpdateVehicles[vehicleId] = true
  end
end

local function onPartsTreeConfigChanged()
  beginVehicleChange(be:getPlayerVehicleID(0))
end

local function materialValuesCallback(vehicleId, values)
  local state = vehicleStates[vehicleId]
  if state then
    MaterialSwitches.onValues(state, values)
  end
end

local function damageCallback(vehicleId, damage)
  local state = vehicleStates[vehicleId]
  if not state or not state.cachedNodeIds or not state.materialIdByName or not state.syncDamage then
    return
  end

  local nodeIndexById = {}
  for index, nodeId in ipairs(state.cachedNodeIds) do
    nodeIndexById[nodeId] = index - 1
  end
  local brokenBeams = {}
  for _, beam in ipairs(damage.brokenBeams or {}) do
    local node1, node2 = nodeIndexById[beam[1]], nodeIndexById[beam[2]]
    if node1 and node2 then
      brokenBeams[#brokenBeams + 1] = {node1, node2}
    end
  end
  local switches = {}
  for _, switch in ipairs(damage.switches or {}) do
    local materialId = state.materialIdByName[switch.material]
    if materialId then
      switches[#switches + 1] = {id = switch.id, materialId = materialId}
    end
  end
  Realtime.updateDamage(state, brokenBeams, damage.hiddenProps or {}, switches)
  MaterialSwitches.applyDamage(state, switches)
end

local function onVehicleDestroyed(vehicleId)
  disabledVehicleIds[vehicleId] = nil
  local tracked = vehicleStates[vehicleId] ~= nil
      or pendingVehicleChanges[vehicleId] ~= nil
      or pendingPaintUpdateVehicles[vehicleId] ~= nil
      or pendingPaintDesignVehicles[vehicleId] ~= nil
      or pendingInitVehicleId == vehicleId
  for i = #initQueue, 1, -1 do
    if initQueue[i] == vehicleId then
      table.remove(initQueue, i)
      tracked = true
    end
  end
  if not tracked then
    return
  end

  if pendingInitVehicleId == vehicleId then
    UDPComm.cancelBulk()
    pendingInitVehicleId = nil
  end
  local state = vehicleStates[vehicleId]
  if state then
    state.phase = VehiclePhase.Removed
  end
  vehicleStates[vehicleId] = nil
  pendingVehicleChanges[vehicleId] = nil
  pendingPaintUpdateVehicles[vehicleId] = nil
  pendingPaintDesignVehicles[vehicleId] = nil
  UDPComm.sendControl('vehicle_removed ' .. tostring(vehicleId))
  completeInitIfReady()
end

local function initContent()
  log('I', logTag, 'Preparing BeamNG scene for BNGLink')
  resetState()
  UDPComm.sendControl('session_reset')

  for i = 0, be:getObjectCount() - 1 do
    local vehicleId = be:getObject(i):getId()
    if isVehicleEnabled(vehicleId) then
      table.insert(initQueue, vehicleId)
    end
  end
  initActive = true
  completeInitIfReady()
end

local function syncContent()
  if initActive and initPending() then
    return
  end

  Realtime.sendFrame(vehicleStates, simClock)
end

local function updateInner(dt)
  if initActive then
    processInitQueue()
  end

  if processPendingVehicleChanges() then
    return
  end

  for vehicleId, state in pairs(vehicleStates) do
    MaterialSwitches.evaluate(vehicleId, state, dt)
  end

  if not initActive then
    processPendingPaintDesignChanges(dt)
    if initActive then
      return
    end

    if checkSkinsAndReinit() then
      return
    end

    paintCheckAccum = paintCheckAccum + dt
    if paintCheckAccum >= PAINT_CHECK_INTERVAL then
      paintCheckAccum = 0
      checkPaintsAndQueueUpdates()
    end
    MaterialSwitches.flushPending(vehicleStates)
    processPendingPaintUpdates()

    local cefInterval = smoothDashboards and SMOOTH_CEF_TEXTURE_CHECK_INTERVAL or PLATE_TEXTURE_CHECK_INTERVAL
    cefTextureCheckAccum = cefTextureCheckAccum + dt
    if cefTextureCheckAccum >= cefInterval then
      cefTextureCheckAccum = cefTextureCheckAccum - cefInterval
      DynamicTextures.updateCef(vehicleStates)
    end

    plateTextureCheckAccum = plateTextureCheckAccum + dt
    if plateTextureCheckAccum >= PLATE_TEXTURE_CHECK_INTERVAL then
      plateTextureCheckAccum = plateTextureCheckAccum - PLATE_TEXTURE_CHECK_INTERVAL
      DynamicTextures.updatePlates(vehicleStates)
    end
  end

  if not doRealtime then return end

  layoutCheckAccum = layoutCheckAccum + dt
  if layoutCheckAccum >= LAYOUT_CHECK_INTERVAL then
    layoutCheckAccum = 0
    if checkLayoutsAndReinit() then
      return
    end
  end

  syncContent()
end

local function onUpdate(dt, dtSim)
  local t0 = Realtime.statsEnabled() and os.clockhp()
  connectionClock = connectionClock + dt
  -- Sending while no receiver listens makes the OS report a refused connection, so only ping an active link
  if linkActive() and connectionClock - lastPingSent >= HEARTBEAT_INTERVAL then
    lastPingSent = connectionClock
    UDPComm.sendControl('ping')
  end
  simClock = simClock + dtSim
  updateInner(dt)
  if t0 then
    Realtime.noteUpdate(os.clockhp() - t0)
  end
end

local function startRealtime()
  if not initActive and not anyVehicleContentReady() then
    log('W', logTag, 'BNGLink sync requested before content was ready, preparing scene')
    initContent()
  end
  if not doRealtime then
    UDPComm.sendControl('realtime_started')
  end
  doRealtime = true
  resumeRealtimeAfterInit = false
  layoutCheckAccum = 0
  paintCheckAccum = 0
  plateTextureCheckAccum = 0
  cefTextureCheckAccum = 0
end

local function stopRealtime()
  if doRealtime then
    UDPComm.sendControl('realtime_stopped')
  end
  doRealtime = false
  resumeRealtimeAfterInit = false
end

-- Only selects the vehicles of the next init, the link itself is driven by startRealtime and resync
local function setVehicleSyncEnabled(vehicleId, enabled)
  disabledVehicleIds[vehicleId] = not enabled or nil
end

local function setSmoothDashboards(enabled)
  smoothDashboards = enabled
  cefTextureCheckAccum = 0
end

local function resync()
  local wasRealtime = doRealtime
  stopRealtime()
  resumeRealtimeAfterInit = wasRealtime
  initContent()
end

local function setConnectionSettings(connectionSettings)
  if not UDPComm.configure(connectionSettings) then
    return false
  end

  lastReceiverResponse = -math.huge
  if linkActive() then
    resync()
  end
  return true
end

local function getUiState()
  local drivenVehicleId = be:getPlayerVehicleID(0)
  local vehicles = {}
  for i = 0, be:getObjectCount() - 1 do
    local vehicle = be:getObject(i)
    local vehicleId = vehicle:getId()
    local jbeamName = vehicle:getJBeamFilename()
    local model = core_vehicles.getModel(jbeamName).model
    vehicles[#vehicles + 1] = {
      id = vehicleId,
      name = model and model.Name or jbeamName,
      enabled = isVehicleEnabled(vehicleId),
      driven = vehicleId == drivenVehicleId,
    }
  end

  local connectionSettings = UDPComm.getSettings()
  return {
    connected = linkActive() and connectionClock - lastReceiverResponse <= CONNECTION_TIMEOUT,
    connectedTo = connectionSettings.receiverHost .. ':' .. connectionSettings.receiverPort,
    connection = connectionSettings,
    vehicles = vehicles,
    liveSync = doRealtime,
    resyncing = initActive,
    smoothDashboards = smoothDashboards,
  }
end

local function isInitActive()
  return initActive
end

local function onExtensionLoaded()
  for _, name in ipairs(SUBMODULES) do
    extensions.load(name)
  end
  extensions.load('core_vehicle_manager')
  extensions.load('core_vehicleBridge')
  extensions.load('core_skiaTemplate')

  UDPComm = extensions.BNGLink_UDPComm
  InitGeometry = extensions.BNGLink_InitGeometry
  InitMaterials = extensions.BNGLink_InitMaterials
  DynamicTextures = extensions.BNGLink_DynamicTextures
  MaterialSwitches = extensions.BNGLink_MaterialSwitches
  Realtime = extensions.BNGLink_Realtime

  UDPComm.setOnReceive(onUdpReceive)
end

local function onExtensionUnloaded()
  UDPComm.setOnReceive(nil)
  for vehicleId in pairs(vehicleStates) do
    local veh = scenetree.findObjectById(vehicleId)
    if veh then
      veh:queueLuaCommand('extensions.unload("BNGLink")')
    end
  end
  resetState()
  initActive = false
  for i = #SUBMODULES, 1, -1 do
    extensions.unload(SUBMODULES[i])
  end
end

M.onExtensionLoaded = onExtensionLoaded
M.onExtensionUnloaded = onExtensionUnloaded
M.onVehicleColorChanged = onVehicleColorChanged
M.onPartsTreeConfigChanged = onPartsTreeConfigChanged
M.onVehicleReplaced = beginVehicleChange
M.onVehicleDestroyed = onVehicleDestroyed
M.onSettingsChanged = onSettingsChanged
M.onSeatOccupantEnter = onSeatOccupantChanged
M.onSeatOccupantExit = onSeatOccupantChanged
M.dynamicDecals_onLayerAdded = onPaintDesignChanged
M.dynamicDecals_onLayerDeleted = onPaintDesignChanged
M.dynamicDecals_onLayerUpdated = onPaintDesignChanged
M.dynamicDecals_setLayer = onPaintDesignChanged
M.dynamicDecals_moveLayer = onPaintDesignChanged
M.onUpdate = onUpdate
M.initContent = initContent
M.startRealtime = startRealtime
M.stopRealtime = stopRealtime
M.resync = resync
M.setVehicleSyncEnabled = setVehicleSyncEnabled
M.setSmoothDashboards = setSmoothDashboards
M.setConnectionSettings = setConnectionSettings
M.getUiState = getUiState
M.materialValuesCallback = materialValuesCallback
M.damageCallback = damageCallback
M.isInitActive = isInitActive

return M
