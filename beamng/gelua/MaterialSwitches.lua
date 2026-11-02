-- Material switches (lights, signals, ...): which primitives each switch drives, and per-frame evaluation of the jbeam trigger expressions from vehicle electrics.

local M = {}

local logTag = 'BNGLink'

local BOOLEAN_MATERIAL_CHECK_INTERVAL = 0.05
local LOW_BEAM_EMISSIVE_SCALE = 0.25
local HIGH_BEAM_EMISSIVE_SCALE = 2
local MATERIAL_CAPTURE_NAME = 'BNGLink_MaterialCapture'

local FFIHelpers
local InitGeometry

local function primitiveMaterialIds(mesh)
  local ids = {}
  local primitives = FFIHelpers.getPrimitives(mesh)
  for primitiveIdx = 0, mesh.primitivesCount - 1 do
    ids[primitiveIdx] = primitives[primitiveIdx].materialId
  end
  return ids
end

local function collectPrimitiveMaterialIds(meshInfo)
  local result = {flex = {}, prop = {}}
  for flexIdx = 0, meshInfo.flexmeshesCount - 1 do
    local flex = meshInfo:flexmeshes(flexIdx)
    result.flex[flex.meshName] = primitiveMaterialIds(flex)
  end
  for propIdx = 0, meshInfo.propmeshesCount - 1 do
    local prop = meshInfo:propmeshes(propIdx)
    result.prop[prop.meshName] = primitiveMaterialIds(prop)
  end
  return result
end

local function recordChangedPrimitiveTargets(targets, baseline, current, msc)
  for kind, meshes in pairs(current) do
    for meshName, materialIds in pairs(meshes) do
      local baselineIds = baseline[kind][meshName]
      if baselineIds then
        local meshTargets = targets[kind][meshName]
        for primitiveIdx, materialId in pairs(materialIds) do
          if materialId ~= baselineIds[primitiveIdx] then
            if not meshTargets then
              meshTargets = {}
              targets[kind][meshName] = meshTargets
            end
            meshTargets[primitiveIdx] = msc
          end
        end
      end
    end
  end
end

local function collectMaterialSwitchIds(vehicleId)
  local vehicleData = core_vehicle_manager.getVehicleData(vehicleId)
  if not vehicleData then
    return nil
  end
  local materialData = vehicleData.vdata._materials
  local switchIds = {}
  local seen = {}
  for _, trigger in ipairs(materialData.triggers) do
    local msc = trigger.msc
    if not seen[msc] and (trigger.on or trigger.on_intense) then
      seen[msc] = true
      switchIds[#switchIds + 1] = msc
    end
  end
  materialData.damageVariants = {}
  for _, beam in pairs(vehicleData.vdata.beams or {}) do
    for msc, switch in pairs(beam.deformSwitches or {}) do
      if not seen[msc] then
        seen[msc] = true
        switchIds[#switchIds + 1] = msc
      end
      if switch.dmgMat and switch.dmgMat ~= '' then
        materialData.damageVariants[switch.dmgMat] = true
      end
    end
  end
  table.sort(switchIds)
  return switchIds, materialData
end

local function ensureMaterialCaptureMarker()
  if scenetree.findObject(MATERIAL_CAPTURE_NAME) then
    return
  end
  local material = createObject('Material')
  material.mapTo = MATERIAL_CAPTURE_NAME
  material.canSave = false
  material:registerObject(MATERIAL_CAPTURE_NAME)
end

-- Finds the primitives each switch drives: snapshot the baseline materials, then switch one id at a time to a marker material and diff the GPUMesh.
-- The mesh only updates a frame after a switch, so this runs across frames and returns true once state.materialSwitchTargets is final.
local function capture(vehicleId, veh, state)
  if state.materialCapture == false then
    return true
  end
  local captureState = state.materialCapture
  if not captureState then
    local switchIds, materialData = collectMaterialSwitchIds(vehicleId)
    if not materialData then
      return false
    end
    state.materialData = materialData
    state.materialSwitchIds = switchIds
    state.materialSwitchTargets = { flex = {}, prop = {} }
    if #switchIds == 0 then
      state.materialCapture = false
      return true
    end
    ensureMaterialCaptureMarker()
    for _, switchId in ipairs(switchIds) do
      veh:resetMaterials(switchId)
    end
    state.materialCapture = {
      phase = 'baseline',
      waitFrames = 1,
      index = 1,
    }
    return false
  end

  if captureState.waitFrames > 0 then
    captureState.waitFrames = captureState.waitFrames - 1
    return false
  end

  if captureState.phase == 'baseline' then
    for _, switchId in ipairs(state.materialSwitchIds) do
      veh:resetMaterials(switchId)
    end
  else
    veh:switchMaterial(state.materialSwitchIds[captureState.index], MATERIAL_CAPTURE_NAME)
  end
  local meshInfo = InitGeometry.readMeshInfo(vehicleId)
  if not meshInfo then
    return false
  end
  local materialIds = collectPrimitiveMaterialIds(meshInfo)
  meshInfo:free()

  if captureState.phase == 'baseline' then
    captureState.baseline = materialIds
  else
    local switchId = state.materialSwitchIds[captureState.index]
    recordChangedPrimitiveTargets(
      state.materialSwitchTargets, captureState.baseline, materialIds, switchId)
    veh:resetMaterials(switchId)
    captureState.index = captureState.index + 1
  end

  local switchId = state.materialSwitchIds[captureState.index]
  if switchId then
    veh:switchMaterial(switchId, MATERIAL_CAPTURE_NAME)
    captureState.phase = 'switch'
    captureState.waitFrames = 1
    return false
  end

  state.materialCapture = false
  return true
end

local function clearCapture(state)
  state.materialCapture = nil
  state.materialSwitchIds = nil
  state.materialSwitchTargets = nil
  state.materialData = nil
end

local function setMaterialValue(sync, key, value)
  if type(value) == 'boolean' then
    if not sync.booleanKeys[key] then
      sync.booleanKeys[key] = true
      sync.booleanSnapshotCommand = nil
    end
    value = value and 1 or 0
  end
  if sync.lastValues[key] == value then
    return
  end
  sync.lastValues[key] = value
  sync.mv[key] = value
  sync.dirty = true
end

local function addSyncKey(syncKeys, syncKeySet, mv, key)
  if not syncKeySet[key] then
    syncKeySet[key] = true
    syncKeys[#syncKeys + 1] = key
  end
  mv[key] = mv[key] or 0
end

local function requestElectricsValue(veh, state, sync, electricsKey)
  extensions.core_vehicleBridge.requestValue(
    veh,
    function(data)
      local current = state.materialSync
      if not current or current.generation ~= sync.generation then
        return
      end
      if data.result ~= nil then
        setMaterialValue(current, electricsKey, data.result)
      end
      current.pendingInitialCount = current.pendingInitialCount - 1
      if current.pendingInitialCount == 0 then
        current.restoreGameMaterials = true
        current.dirty = true
      end
    end,
    'electricsValue',
    electricsKey
  )
end

-- Boolean electrics are snapshotted in the vehicle VM instead of read from the vehicleBridge cache
-- The vehicle calls back into the orchestrator, which forwards to onValues
local function getBooleanSnapshotCommand(vehicleId, sync)
  if sync.booleanSnapshotCommand then
    return sync.booleanSnapshotCommand
  end
  local keys = {}
  for key in pairs(sync.booleanKeys) do
    keys[#keys + 1] = key
  end
  table.sort(keys)
  sync.booleanSnapshotCommand =
    'local keys=' .. serialize(keys)
    .. ';local values={};for _,key in ipairs(keys) do values[key]=electrics.values[key] end;'
    .. "obj:queueGameEngineLua('extensions.BNGLink_BNGLink.materialValuesCallback("
    .. tostring(vehicleId) .. ",'..serialize(values)..')')"
  return sync.booleanSnapshotCommand
end

-- Compiles the trigger expressions against a local copy of the electrics values and subscribes to every electrics key they read.
-- materialIdByName maps material names to exported ids.
local function setup(vehicleId, veh, state, materialIdByName)
  state.materialSync = nil
  local materialData = state.materialData

  local mv = deepcopy(materialData.mv)
  local triggers = deepcopy(materialData.triggers)
  local syncKeys = deepcopy(materialData.triggerList)
  local syncKeySet = {}
  for _, key in ipairs(syncKeys) do
    syncKeySet[key] = true
  end

  local needsBeamMode = false
  for _, trigger in ipairs(triggers) do
    trigger.offMaterialId = materialIdByName[trigger.off]
    trigger.onMaterialId = materialIdByName[trigger.on]
    trigger.onIntenseMaterialId = materialIdByName[trigger.on_intense]
    local expression = (trigger.evalFunctionString or ''):lower()
    local materialName = trigger.orgMat:lower()
    local headlightMaterial =
      materialName:find('headlight', 1, true) or materialName:find('sealedbeam', 1, true)
    trigger.beamModeAware =
      headlightMaterial
      and (expression:find('lowhighbeam', 1, true)
        or expression:find('lowbeam', 1, true)
        or expression:find('highbeam', 1, true))
    -- Also tracks the raw turn signal so the switch turns off as soon as the signal does
    local immediateSignalOff =
      materialName:find('signal', 1, true)
      and (expression:find('signal_l_filament', 1, true)
        or expression:find('signal_r_filament', 1, true))
      and not expression:find('lowhigh', 1, true)
      and not expression:find('brake', 1, true)
    if immediateSignalOff then
      trigger.immediateOffKey =
        expression:find('signal_l_filament', 1, true) and 'signal_L' or 'signal_R'
      addSyncKey(syncKeys, syncKeySet, mv, trigger.immediateOffKey)
    end
    needsBeamMode = needsBeamMode or trigger.beamModeAware
    trigger.syncState = {}
    local source = 'return function () ' .. (trigger.evalFunctionString or '') .. ' end'
    local factory, err = load(source, 'BNGLink material switch', 't', mv)
    if factory then
      trigger.evalFunction = factory()
    else
      log('E', logTag, 'Could not compile material switch: ' .. tostring(err))
      trigger.evalFunction = nop
    end
  end
  if needsBeamMode then
    addSyncKey(syncKeys, syncKeySet, mv, 'lowbeam')
    addSyncKey(syncKeys, syncKeySet, mv, 'highbeam')
  end

  local sync = {
    keys = syncKeys,
    mv = mv,
    triggers = triggers,
    lastValues = {},
    lastSent = {},
    states = {},
    damaged = {},
    needsBeamMode = needsBeamMode,
    booleanKeys = {},
    pendingInitialCount = #syncKeys,
    booleanCheckAccum = 0,
    dirty = true,
    generation = (state.materialSyncGeneration or 0) + 1,
  }
  state.materialSyncGeneration = sync.generation
  state.materialSync = sync
  -- The capture left the game materials reset, restore them once every initial value arrived
  sync.restoreGameMaterials = sync.pendingInitialCount == 0

  for _, key in ipairs(sync.keys) do
    extensions.core_vehicleBridge.registerValueChangeNotification(veh, key)
    requestElectricsValue(veh, state, sync, key)
  end
end

local function onValues(state, values)
  local sync = state.materialSync
  if not sync then
    return
  end
  for key, value in pairs(values) do
    if sync.booleanKeys[key] then
      setMaterialValue(sync, key, value)
    end
  end
end

local function evaluate(vehicleId, state, dt)
  local sync = state.materialSync
  if not sync then
    return
  end

  if next(sync.booleanKeys) then
    sync.booleanCheckAccum = sync.booleanCheckAccum + dt
    if sync.booleanCheckAccum >= BOOLEAN_MATERIAL_CHECK_INTERVAL then
      sync.booleanCheckAccum = 0
      scenetree.findObjectById(vehicleId):queueLuaCommand(getBooleanSnapshotCommand(vehicleId, sync))
    end
  end

  for _, key in ipairs(sync.keys) do
    if not sync.booleanKeys[key] then
      local value = extensions.core_vehicleBridge.getCachedVehicleData(vehicleId, key)
      if value ~= nil then
        setMaterialValue(sync, key, value)
      end
    end
  end
  if not sync.dirty then
    return
  end
  sync.dirty = false

  local states = sync.states
  table.clear(states)
  local lowBeam, highBeam
  if sync.needsBeamMode then
    lowBeam = sync.mv.lowbeam > 0.5
    highBeam = sync.mv.highbeam > 0.5
  end
  for _, trigger in ipairs(sync.triggers) do
    local value = trigger.evalFunction()
    if trigger.immediateOffKey and sync.mv[trigger.immediateOffKey] <= 0 then
      value = 0
    end
    if value ~= nil then
      local materialId = trigger.offMaterialId
      local emissiveScale = 0
      local active = false
      local useIntense = trigger.onIntenseMaterialId ~= nil and value > 0.5
      local beamScale = 1
      if trigger.beamModeAware then
        if not lowBeam and not highBeam then
          value = 0
        end
        useIntense = highBeam and trigger.onIntenseMaterialId ~= nil
        beamScale = highBeam and HIGH_BEAM_EMISSIVE_SCALE or LOW_BEAM_EMISSIVE_SCALE
      end
      if value > 0.0001 then
        local activeMaterialId = trigger.onMaterialId
        local scaling = trigger.materialEmissiveScaling
        local emissiveMax = scaling and scaling.on_max
        if useIntense then
          activeMaterialId = trigger.onIntenseMaterialId
          emissiveMax = scaling and scaling.on_intense_max
        end
        if activeMaterialId ~= nil then
          active = true
          materialId = activeMaterialId
          if emissiveMax then
            emissiveScale = math.floor(clamp(value / emissiveMax, 0, 1) * 255 + 0.5) / 255
          else
            emissiveScale = 1
          end
          emissiveScale = emissiveScale * beamScale
        end
      end
      local newState = trigger.syncState
      newState.materialId = materialId
      newState.emissiveScale = emissiveScale
      newState.materialName = active and (useIntense and trigger.on_intense or trigger.on) or nil
      newState.active = active
      -- Several triggers can share a switch id, an active one wins over an inactive one
      if materialId ~= nil and (active or states[trigger.msc] == nil) then
        states[trigger.msc] = newState
      end
    end
  end
  for switchId, switchState in pairs(sync.damaged) do
    states[switchId] = switchState
  end

  if sync.restoreGameMaterials then
    local veh = scenetree.findObjectById(vehicleId)
    for switchId, switchState in pairs(states) do
      if switchState.active and switchState.materialName then
        veh:switchMaterial(switchId, switchState.materialName)
      else
        veh:resetMaterials(switchId)
      end
    end
    sync.restoreGameMaterials = false
  end

  local updates
  for switchId, switchState in pairs(states) do
    local previous = sync.lastSent[switchId]
    if not previous
        or previous.materialId ~= switchState.materialId
        or previous.emissiveScale ~= switchState.emissiveScale then
      updates = updates or {}
      updates[#updates + 1] = {
        id = switchId,
        materialId = switchState.materialId,
        emissiveScale = switchState.emissiveScale,
        damaged = switchState.damaged,
      }
    end
  end
  if updates then
    sync.pending = updates
  end
end

local function applyDamage(state, switches)
  local sync = state.materialSync
  if not sync then
    return
  end
  local damaged = {}
  local updates = {}
  for _, switch in ipairs(switches) do
    local previous = sync.damaged[switch.id]
    local switchState = {
      id = switch.id,
      materialId = switch.materialId,
      emissiveScale = 0,
      active = true,
      damaged = true,
    }
    damaged[switch.id] = switchState
    if not previous or previous.materialId ~= switch.materialId then
      updates[#updates + 1] = switchState
    end
  end
  for switchId in pairs(sync.damaged) do
    if not damaged[switchId] then
      updates[#updates + 1] = {
        id = switchId,
        emissiveScale = 0,
        damaged = false,
      }
    end
  end
  sync.damaged = damaged
  if #updates > 0 then
    sync.dirty = true
    sync.pending = updates
  end
end

local function flushPending(vehicleStates)
  for vehicleId, state in pairs(vehicleStates) do
    local sync = state.materialSync
    if state.contentReady and sync and sync.pending then
      extensions.BNGLink_UDPComm.sendUpdate('', {type = 'material_update', vehicleId = vehicleId, switches = sync.pending})
      for _, update in ipairs(sync.pending) do
        sync.lastSent[update.id] = update
      end
      sync.pending = nil
    end
  end
end

local function onExtensionLoaded()
  FFIHelpers = extensions.BNGLink_FFIHelpers
  InitGeometry = extensions.BNGLink_InitGeometry
end

M.onExtensionLoaded = onExtensionLoaded
M.capture = capture
M.clearCapture = clearCapture
M.setup = setup
M.onValues = onValues
M.evaluate = evaluate
M.applyDamage = applyDamage
M.flushPending = flushPending

return M
