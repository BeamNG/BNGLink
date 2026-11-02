-- Textures that change at runtime (license plates, dynamic decals, CEF screens).
-- Re-reads them and sends a texture_update with only the entries whose bytes changed.

local M = {}

local InitMaterials

local Result = {
  None = 'none',
  Unchanged = 'unchanged',
  Retry = 'retry',
  Sent = 'sent',
}

local pendingPlateVehicles = {}

local function track(state, textureList)
  local byKind = {}
  for index, entry in ipairs(textureList) do
    local kind = entry.dynamicKind
    if kind then
      byKind[kind] = byKind[kind] or {}
      byKind[kind][index - 1] = entry
    end
  end
  state.dynamicTextures = byKind
end

local function sendUpdate(vehicleId, state, dynamicKind)
  local dynamicTextures = state and state.contentReady and state.dynamicTextures[dynamicKind]
  if not dynamicTextures then
    return Result.None
  end
  local veh = scenetree.findObjectById(vehicleId)
  if dynamicKind == 'decal' then
    InitMaterials.invalidateDecalExport()
  end
  local activeCefTextures
  if dynamicKind == 'cef' then
    activeCefTextures = {}
    for _, tag in ipairs(veh:getActiveUITextureTagNames()) do
      activeCefTextures[tag] = true
    end
  end

  local payloadParts, descriptors, replacements = {}, {}, {}
  local offset = 0
  for index, oldEntry in pairs(dynamicTextures) do
    if not activeCefTextures or activeCefTextures[oldEntry.source] then
      local entry = InitMaterials.readTextureEntry(oldEntry.source, veh)
      if entry and entry.data ~= oldEntry.data then
        descriptors[#descriptors + 1] = {index = index, offset = offset, size = #entry.data, mime = entry.mime, name = entry.name}
        payloadParts[#payloadParts + 1] = entry.data
        replacements[index] = entry
        offset = offset + #entry.data
      end
    end
  end
  if #descriptors == 0 then
    return Result.Unchanged
  end

  local meta = {type = 'texture_update', vehicleId = vehicleId, textures = descriptors}
  if not extensions.BNGLink_UDPComm.sendBulk(table.concat(payloadParts), meta) then
    return Result.Retry
  end
  for index, entry in pairs(replacements) do
    dynamicTextures[index] = entry
  end
  return Result.Sent
end

local function updateCef(vehicleStates)
  for vehicleId, state in pairs(vehicleStates) do
    if sendUpdate(vehicleId, state, 'cef') == Result.Retry then
      return
    end
  end
end

local function updatePlates(vehicleStates)
  for vehicleId in pairs(pendingPlateVehicles) do
    if sendUpdate(vehicleId, vehicleStates[vehicleId], 'plate') == Result.Retry then
      return
    end
    pendingPlateVehicles[vehicleId] = nil
  end
end

local function reset()
  pendingPlateVehicles = {}
end

local function onLicensePlateChanged(_, vehicleId)
  InitMaterials.invalidatePlateTextures(vehicleId)
  pendingPlateVehicles[vehicleId] = true
end

local function onExtensionLoaded()
  InitMaterials = extensions.BNGLink_InitMaterials
end

M.onExtensionLoaded = onExtensionLoaded
M.onLicensePlateChanged = onLicensePlateChanged
M.Result = Result
M.track = track
M.sendUpdate = sendUpdate
M.updateCef = updateCef
M.updatePlates = updatePlates
M.reset = reset

return M
