local M = {}

local brokenBeams = {}
local damagedSwitches = {}
local dirty = false

local function collectBeam(beam, includeBreak)
  if not beam then
    return
  end
  if includeBreak and not beam.disableMeshBreaking and obj:beamIsBroken(beam.cid) then
    brokenBeams[beam.cid] = {
      math.min(beam.id1, beam.id2),
      math.max(beam.id1, beam.id2),
    }
  end
  for switchId, switch in pairs(beam.deformSwitches or {}) do
    damagedSwitches[switchId] = switch.dmgMat
  end
  dirty = true
end

local function sendDamage()
  if not dirty then
    return
  end

  local beamIds = tableKeysSorted(brokenBeams)
  local beams = {}
  for _, beamId in ipairs(beamIds) do
    beams[#beams + 1] = brokenBeams[beamId]
  end

  local switchIds = tableKeysSorted(damagedSwitches)
  local switches = {}
  for _, switchId in ipairs(switchIds) do
    switches[#switches + 1] = {id = switchId, material = damagedSwitches[switchId]}
  end

  local hiddenProps = {}
  for pid, prop in pairs(v.data.props or {}) do
    if prop.hidden then
      hiddenProps[#hiddenProps + 1] = pid
    end
  end
  table.sort(hiddenProps)

  obj:queueGameEngineLua(
    'extensions.BNGLink_BNGLink.damageCallback(' .. tostring(objectId) .. ',' ..
    serialize({brokenBeams = beams, switches = switches, hiddenProps = hiddenProps}) .. ')'
  )
  dirty = false
end

local function clearDamage()
  brokenBeams = {}
  damagedSwitches = {}
  dirty = true
end

local function collectCurrentDamage()
  clearDamage()
  for _, beam in pairs(v.data.beams or {}) do
    if obj:beamIsBroken(beam.cid) then
      collectBeam(beam, true)
    end
  end
  for _, beamId in pairs(beamstate.deformGroupsTriggerBeam) do
    collectBeam(v.data.beams[beamId], false)
  end
end

local function onBeamBroke(id)
  collectBeam(v.data.beams[id], true)
end

local function onBeamDeformed(id)
  collectBeam(v.data.beams[id], false)
end

local function onReset()
  clearDamage()
end

local function onTorsionbarBroken()
  dirty = true
end

M.onExtensionLoaded = collectCurrentDamage
M.onBeamBroke = onBeamBroke
M.onBeamDeformed = onBeamDeformed
M.onTorsionbarBroken = onTorsionbarBroken
M.onReset = onReset
M.updateGFX = sendDamage

return M
