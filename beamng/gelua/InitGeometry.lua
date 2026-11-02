-- Init geometry: reads the GPUMesh snapshot once and builds the body parts, skinning, props cache and the geometry section of the init payload.

local M = {}

local ffi = require('ffi')
local min = math.min

local logTag = 'BNGLink'

local skinEntryArray = ffi.typeof('struct { int32_t nodes[4]; float coords[3]; }[?]')
local floatArray = ffi.typeof('float[?]')
local uintArray = ffi.typeof('unsigned int[?]')
local int32Array = ffi.typeof('int32_t[?]')
local occupantMeshKinds = {character_torso = true, character_head = true}

local FFIHelpers

local function getHiddenOccupantMeshes(vehicleId)
  local visiblePartPaths = {}
  if settings.getValue('enableSeatOccupants', true) then
    for _, seat in ipairs(gameplay_seatOccupants.getSeats(vehicleId)) do
      if seat.occupied and seat.partPath then
        visiblePartPaths[seat.partPath] = true
      end
    end
  end

  local hiddenMeshes = {}
  local vdata = core_vehicle_manager.getVehicleData(vehicleId).vdata
  local function hideOccupantMesh(meshData)
    local mesh = meshData.mesh
    if mesh and occupantMeshKinds[meshData.originalMesh or mesh] and not visiblePartPaths[meshData.partPath] then
      hiddenMeshes[FFIHelpers.normalizeMeshName(mesh)] = true
    end
  end

  for _, flexbody in pairs(vdata.flexbodies or {}) do hideOccupantMesh(flexbody) end
  for _, prop in pairs(vdata.props or {}) do hideOccupantMesh(prop) end
  return hiddenMeshes
end

local function readMeshInfo(vehicleId)
  local meshInfo = GPUMesh.bng_getGPUMesh(vehicleId)
  if not meshInfo:dataIsReady() then
    meshInfo:free()
    return nil
  end
  return meshInfo
end

-- Checks if the vehicle has changed since the last call
local function layoutSignature(veh, hiddenOccupantMeshes)
  hiddenOccupantMeshes = hiddenOccupantMeshes or getHiddenOccupantMeshes(veh:getId())
  local flexMeshes = {}
  for i = 0, veh:getFlexmeshCount() - 1 do
    local flexObj = veh:getFlexmesh(i)
    local meshName = flexObj:getMeshName()
    if not hiddenOccupantMeshes[FFIHelpers.normalizeMeshName(meshName)] then
      flexMeshes[#flexMeshes + 1] = meshName .. ':' .. flexObj:getVertexCount()
    end
  end
  table.sort(flexMeshes)

  local propNames = {}
  for i = 0, veh:getPropCount() - 1 do
    local meshName = veh:getProp(i):getMeshName()
    if not hiddenOccupantMeshes[FFIHelpers.normalizeMeshName(meshName)] then
      propNames[#propNames + 1] = meshName
    end
  end
  table.sort(propNames)

  return table.concat(flexMeshes, '|') .. '#' .. table.concat(propNames, '|')
end

local function buildFlexObjCache(veh)
  local cache = {}
  for flexMeshIdx = 0, veh:getFlexmeshCount() - 1 do
    local flexObj = veh:getFlexmesh(flexMeshIdx)
    local meshName = flexObj:getMeshName()
    if meshName ~= '' and not cache[meshName] then
      cache[meshName] = flexObj
      cache[FFIHelpers.normalizeMeshName(meshName)] = flexObj
    end
  end
  return cache
end

-- GPUMesh indices are global while flexmesh locators are local, find the offset that maps every corner in range
local function inferVertexOffset(part, flexVertCount)
  local indices = part.sourceIndices
  local primitives = part.primitives

  for offset = part.globalMaxIdx - flexVertCount + 1, part.globalMinIdx, -1 do
    local ok = true
    for primitiveIdx = 0, part.primitiveCount - 1 do
      local primitive = primitives[primitiveIdx]
      local indexEnd = min(primitive.startIndex + primitive.indexCount, part.sourceIndexCount)
      for idx = primitive.startIndex, indexEnd - 1 do
        local flexIdx = indices[idx] - offset
        if flexIdx < 0 or flexIdx >= flexVertCount then
          ok = false
          break
        end
      end
      if not ok then
        break
      end
    end
    if ok then
      return offset
    end
  end
  return nil
end

-- Primitive ranges of the flexmesh plus the switch id of every primitive
local function collectFlexIndices(indices, indicesCount, flexmesh, switchIdsByPrimitive)
  local primitives = FFIHelpers.getPrimitives(flexmesh)
  local cornerIndexCount = 0
  local triangleCount = 0
  for primitiveIdx = 0, flexmesh.primitivesCount - 1 do
    local primitive = primitives[primitiveIdx]
    local indexEnd = min(primitive.startIndex + primitive.indexCount, indicesCount)
    if indexEnd > primitive.startIndex then
      local primitiveIndexCount = indexEnd - primitive.startIndex
      cornerIndexCount = cornerIndexCount + primitiveIndexCount
      triangleCount = triangleCount + math.floor(primitiveIndexCount / 3)
    end
  end
  local switchIds = int32Array(flexmesh.primitivesCount)
  for primitiveIdx = 0, flexmesh.primitivesCount - 1 do
    switchIds[primitiveIdx] = switchIdsByPrimitive[primitiveIdx] or -1
  end
  return primitives, flexmesh.primitivesCount, switchIds, cornerIndexCount, triangleCount
end

local function collectPropMaterialIds(gpuProp, switchIdsByPrimitive)
  local primitives = FFIHelpers.getPrimitives(gpuProp)
  local switchIds = int32Array(gpuProp.primitivesCount)
  local triangleCount = math.floor(gpuProp.indicesCount / 3)
  local coveredUntil = 0
  local hasDefaultMaterial = false
  for primitiveIdx = 0, gpuProp.primitivesCount - 1 do
    local primitive = primitives[primitiveIdx]
    switchIds[primitiveIdx] = switchIdsByPrimitive[primitiveIdx] or -1
    local firstTriangle = math.floor(primitive.startIndex / 3)
    local primitiveTriangleCount = math.floor(primitive.indexCount / 3)
    if primitiveTriangleCount > 0 then
      if firstTriangle > coveredUntil then
        hasDefaultMaterial = true
      end
      local primitiveEnd = firstTriangle + primitiveTriangleCount
      if primitiveEnd > coveredUntil then
        coveredUntil = primitiveEnd
      end
    end
  end
  if coveredUntil < triangleCount then
    hasDefaultMaterial = true
  end
  return primitives, gpuProp.primitivesCount, switchIds, triangleCount, hasDefaultMaterial
end

-- One part per flexmesh with triangles, sorted by name.
-- Vertices are deduplicated per part: uniqueGlobals holds the global index of each part vertex.
local function buildParts(meshInfo, flexSwitchTargets, hiddenOccupantMeshes)
  local parts = {}
  local indices = FFIHelpers.readArray(meshInfo, 'indicesGet', 'unsigned int[?]', meshInfo.indicesCount)
  local globalToUnique = uintArray(meshInfo.verticesCount)
  local touchedGlobals = uintArray(meshInfo.verticesCount)

  for flexIdx = 0, meshInfo.flexmeshesCount - 1 do
    local flex = meshInfo:flexmeshes(flexIdx)
    if not hiddenOccupantMeshes[FFIHelpers.normalizeMeshName(flex.meshName)] then
      local switchIdsByPrimitive = FFIHelpers.findByMeshName(flexSwitchTargets, flex.meshName) or {}
      local primitives, primitiveCount, switchIds, cornerIndexCount, triangleCount = collectFlexIndices(
        indices, meshInfo.indicesCount, flex, switchIdsByPrimitive)

      if cornerIndexCount > 0 then
        local uniqueCount = 0
        local globalMinIdx, globalMaxIdx

        for primitiveIdx = 0, primitiveCount - 1 do
          local primitive = primitives[primitiveIdx]
          local indexEnd = min(primitive.startIndex + primitive.indexCount, meshInfo.indicesCount)
          for idx = primitive.startIndex, indexEnd - 1 do
            local globalIdx = indices[idx]
            if not globalMinIdx or globalIdx < globalMinIdx then globalMinIdx = globalIdx end
            if not globalMaxIdx or globalIdx > globalMaxIdx then globalMaxIdx = globalIdx end
            local uniqueIdx = globalToUnique[globalIdx]
            if uniqueIdx == 0 then
              uniqueCount = uniqueCount + 1
              globalToUnique[globalIdx] = uniqueCount
              touchedGlobals[uniqueCount - 1] = globalIdx
            end
          end
        end

        local uniqueGlobals = uintArray(uniqueCount)
        for i = 0, uniqueCount - 1 do
          uniqueGlobals[i] = touchedGlobals[i]
        end
        for i = 0, uniqueCount - 1 do
          globalToUnique[touchedGlobals[i]] = 0
        end

        parts[#parts + 1] = {
          name = flex.meshName,
          sourceIndices = indices,
          sourceIndexCount = meshInfo.indicesCount,
          primitives = primitives,
          primitiveCount = primitiveCount,
          cornerIndexCount = cornerIndexCount,
          switchIds = switchIds,
          triangleCount = triangleCount,
          globalMinIdx = globalMinIdx,
          globalMaxIdx = globalMaxIdx,
          uniqueGlobals = uniqueGlobals,
          uniqueGlobalCount = uniqueCount,
        }
      end
    end
  end

  table.sort(parts, function(a, b) return a.name < b.name end)
  return parts
end

local function findPropPidByMesh(veh, meshName)
  local norm = FFIHelpers.normalizeMeshName(meshName)
  for propIdx = 0, veh:getPropCount() - 1 do
    local name = veh:getProp(propIdx):getMeshName()
    if name == meshName or FFIHelpers.normalizeMeshName(name) == norm then
      return propIdx
    end
  end
  return nil
end

-- GPUMesh props matched to a vehicle prop, sorted by name
local function buildPropsCache(veh, meshInfo, propSwitchTargets, hiddenOccupantMeshes)
  local cachedProps = {}
  for propIdx = 0, meshInfo.propmeshesCount - 1 do
    local gpuProp = meshInfo:propmeshes(propIdx)
    if not hiddenOccupantMeshes[FFIHelpers.normalizeMeshName(gpuProp.meshName)] then
      local pid = findPropPidByMesh(veh, gpuProp.meshName)
      if pid then
        local propObj = veh:getProp(pid)
        local bindMat = gpuProp.rotation:getMatrix()
        bindMat:setPosition(gpuProp.position)
        local primitives, primitiveCount, switchIds, triangleCount, hasDefaultMaterial = collectPropMaterialIds(
          gpuProp, FFIHelpers.findByMeshName(propSwitchTargets, gpuProp.meshName) or {})
        cachedProps[#cachedProps + 1] = {
          pid = pid,
          propObj = propObj,
          name = FFIHelpers.normalizeMeshName(gpuProp.meshName),
          primitives = primitives,
          primitiveCount = primitiveCount,
          switchIds = switchIds,
          triangleCount = triangleCount,
          hasDefaultMaterial = hasDefaultMaterial,
          indexCount = gpuProp.indicesCount,
          bindMatrix = FFIHelpers.matrixTo12(bindMat),
          liveInitMatrix = FFIHelpers.matrixTo12(propObj:getLiveTransformWorld()),
          gpuPropIdx = propIdx,
        }
      end
    end
  end

  table.sort(cachedProps, function(a, b) return a.name < b.name end)
  return cachedProps
end

local function findFlexbodyByMesh(vdata, meshName)
  local norm = FFIHelpers.normalizeMeshName(meshName)
  for _, flexbody in pairs(vdata.flexbodies) do
    if flexbody.mesh == meshName or flexbody.originalMesh == meshName
        or flexbody.mesh == norm or flexbody.originalMesh == norm then
      return flexbody
    end
  end
  return nil
end

-- Sets part.skinning on every part that could be matched to a flexmesh
-- returns the sorted node ids they use
local function buildSkinning(veh, vehicleId, parts)
  local vdata = core_vehicle_manager.getVehicleData(vehicleId).vdata
  local flexObjCache = buildFlexObjCache(veh)
  local nodeIdSet = {}

  for _, part in ipairs(parts) do
    local flexObj = FFIHelpers.findByMeshName(flexObjCache, part.name)
    if not flexObj then
      local flexbody = findFlexbodyByMesh(vdata, part.name)
      flexObj = flexbody and flexbody.fid >= 0 and veh:getFlexmesh(flexbody.fid) or nil
    end
    local vertexOffset = flexObj and inferVertexOffset(part, flexObj:getVertexCount())
    if vertexOffset then
      local entries = skinEntryArray(part.uniqueGlobalCount)
      for i = 0, part.uniqueGlobalCount - 1 do
        local globalIdx = part.uniqueGlobals[i]
        local loc = flexObj:getVertexLocatorInfo(globalIdx - vertexOffset)
        local entry = entries[i]
        for ni = 0, 3 do
          local nodeId = loc.nodes[ni + 1]
          entry.nodes[ni] = nodeId
          if nodeId >= 0 then
            nodeIdSet[nodeId] = true
          end
        end
        entry.coords[0], entry.coords[1], entry.coords[2] = loc.coords.x, loc.coords.y, loc.coords.z
      end
      part.skinning = ffi.string(entries, ffi.sizeof(entries))
    end
  end

  local nodeIds = {}
  for nodeId in pairs(nodeIdSet) do
    nodeIds[#nodeIds + 1] = nodeId
  end
  table.sort(nodeIds)

  log('I', logTag, string.format('Prepared skinning for vehicle %d (%d nodes)', vehicleId, #nodeIds))
  return nodeIds
end

local function appendPartMaterialIds(ids, idSet, part)
  local primitives = part.primitives
  for primitiveIdx = 0, part.primitiveCount - 1 do
    local primitive = primitives[primitiveIdx]
    local indexEnd = min(primitive.startIndex + primitive.indexCount, part.sourceIndexCount)
    if indexEnd - primitive.startIndex >= 3 then
      local matId = primitive.materialId
      if not idSet[matId] then
        idSet[matId] = true
        ids[#ids + 1] = matId
      end
    end
  end
end

local function appendPropMaterialIds(ids, idSet, prop)
  if prop.hasDefaultMaterial and not idSet[0] then
    idSet[0] = true
    ids[#ids + 1] = 0
  end
  local primitives = prop.primitives
  for primitiveIdx = 0, prop.primitiveCount - 1 do
    local primitive = primitives[primitiveIdx]
    if primitive.indexCount >= 3 then
      local matId = primitive.materialId
      if not idSet[matId] then
        idSet[matId] = true
        ids[#ids + 1] = matId
      end
    end
  end
end

local function collectUsedMaterialIds(parts, cachedProps)
  local ids = {}
  local idSet = {}
  for _, part in ipairs(parts) do
    if part.skinning then
      appendPartMaterialIds(ids, idSet, part)
    end
  end
  for _, prop in ipairs(cachedProps) do
    appendPropMaterialIds(ids, idSet, prop)
  end
  table.sort(ids)
  return ids
end

local function addStaticBytes(payload, keyParts, bytes)
  payload[#payload + 1] = bytes
  keyParts[#keyParts + 1] = bytes
end

local function readFloatBytes(obj, getter, count)
  return ffi.string(FFIHelpers.readArray(obj, getter, 'float[?]', count), count * 4)
end

local function gatherFloatsByCount(src, indices, count, width)
  local buf = floatArray(count * width)
  for i = 0, count - 1 do
    local s, d = indices[i] * width, i * width
    for c = 0, width - 1 do
      buf[d + c] = src[s + c]
    end
  end
  return ffi.string(buf, count * width * 4)
end

local function uint32sPartIndices(part, globalToUnique)
  for i = 0, part.uniqueGlobalCount - 1 do
    globalToUnique[part.uniqueGlobals[i]] = i + 1
  end
  local buf = uintArray(part.cornerIndexCount)
  local indices = part.sourceIndices
  local primitives = part.primitives
  local outIdx = 0
  for primitiveIdx = 0, part.primitiveCount - 1 do
    local primitive = primitives[primitiveIdx]
    local indexEnd = min(primitive.startIndex + primitive.indexCount, part.sourceIndexCount)
    for idx = primitive.startIndex, indexEnd - 1 do
      buf[outIdx] = globalToUnique[indices[idx]] - 1
      outIdx = outIdx + 1
    end
  end
  for i = 0, part.uniqueGlobalCount - 1 do
    globalToUnique[part.uniqueGlobals[i]] = 0
  end
  return ffi.string(buf, part.cornerIndexCount * 4)
end

local function gatherPartCornerFloats(src, part, width)
  local buf = floatArray(part.cornerIndexCount * width)
  local indices = part.sourceIndices
  local primitives = part.primitives
  local outIdx = 0
  for primitiveIdx = 0, part.primitiveCount - 1 do
    local primitive = primitives[primitiveIdx]
    local indexEnd = min(primitive.startIndex + primitive.indexCount, part.sourceIndexCount)
    for idx = primitive.startIndex, indexEnd - 1 do
      local s, d = indices[idx] * width, outIdx * width
      for c = 0, width - 1 do
        buf[d + c] = src[s + c]
      end
      outIdx = outIdx + 1
    end
  end
  return ffi.string(buf, part.cornerIndexCount * width * 4)
end

local function uint32sPartMaterialIds(part, remap)
  local buf = uintArray(part.triangleCount)
  local outIdx = 0
  local primitives = part.primitives
  for primitiveIdx = 0, part.primitiveCount - 1 do
    local primitive = primitives[primitiveIdx]
    local indexEnd = min(primitive.startIndex + primitive.indexCount, part.sourceIndexCount)
    local matId = primitive.materialId
    for _ = 1, math.floor((indexEnd - primitive.startIndex) / 3) do
      buf[outIdx] = remap[matId]
      outIdx = outIdx + 1
    end
  end
  return ffi.string(buf, part.triangleCount * 4)
end

local function uint32sPartSwitchIds(part)
  local buf = uintArray(part.triangleCount)
  local outIdx = 0
  local primitives = part.primitives
  for primitiveIdx = 0, part.primitiveCount - 1 do
    local primitive = primitives[primitiveIdx]
    local indexEnd = min(primitive.startIndex + primitive.indexCount, part.sourceIndexCount)
    local switchId = part.switchIds[primitiveIdx] + 1
    for _ = 1, math.floor((indexEnd - primitive.startIndex) / 3) do
      buf[outIdx] = switchId
      outIdx = outIdx + 1
    end
  end
  return ffi.string(buf, part.triangleCount * 4)
end

local function uint32sPropMaterialIds(prop, remap)
  local buf = uintArray(prop.triangleCount)
  local defaultMat = remap[0] or 0
  for i = 0, prop.triangleCount - 1 do
    buf[i] = defaultMat
  end
  local primitives = prop.primitives
  for primitiveIdx = 0, prop.primitiveCount - 1 do
    local primitive = primitives[primitiveIdx]
    local firstTriangle = math.floor(primitive.startIndex / 3)
    local primitiveTriangleCount = math.floor(primitive.indexCount / 3)
    local matId = remap[primitive.materialId]
    for i = 0, primitiveTriangleCount - 1 do
      buf[firstTriangle + i] = matId
    end
  end
  return ffi.string(buf, prop.triangleCount * 4)
end

local function uint32sPropSwitchIds(prop)
  local buf = uintArray(prop.triangleCount)
  for i = 0, prop.triangleCount - 1 do
    buf[i] = 0
  end
  local primitives = prop.primitives
  for primitiveIdx = 0, prop.primitiveCount - 1 do
    local primitive = primitives[primitiveIdx]
    local firstTriangle = math.floor(primitive.startIndex / 3)
    local primitiveTriangleCount = math.floor(primitive.indexCount / 3)
    local switchId = prop.switchIds[primitiveIdx] + 1
    for i = 0, primitiveTriangleCount - 1 do
      buf[firstTriangle + i] = switchId
    end
  end
  return ffi.string(buf, prop.triangleCount * 4)
end

-- switchTargets: primitive -> material switch id per mesh name, from MaterialSwitches.capture
local function build(veh, vehicleId, meshInfo, switchTargets)
  local readArray = FFIHelpers.readArray
  local hiddenOccupantMeshes = getHiddenOccupantMeshes(vehicleId)
  local parts = buildParts(meshInfo, switchTargets.flex, hiddenOccupantMeshes)
  local nodeIds = buildSkinning(veh, vehicleId, parts)
  local props = buildPropsCache(veh, meshInfo, switchTargets.prop, hiddenOccupantMeshes)

  -- Materials are exported in usedMaterialIds order, so the wire material id is the position in that list
  local usedMaterialIds = collectUsedMaterialIds(parts, props)
  local materialIndexById = {}
  for i, matId in ipairs(usedMaterialIds) do
    materialIndexById[matId] = i - 1
  end

  -- Flex vertex data follows the deformation, so the geometry key leaves it out
  local payload, keyParts = {}, {}

  local vertices = readArray(meshInfo, 'verticesGet', 'float[?]', meshInfo.verticesCount * 3)
  local normals = readArray(meshInfo, 'normalsGet', 'float[?]', meshInfo.normalsCount * 3)
  local tangents = readArray(meshInfo, 'tangentsGet', 'float[?]', meshInfo.tangentsCount * 4)
  local uvs = readArray(meshInfo, 'uv1Get', 'float[?]', meshInfo.uv1Count * 2)
  local uvs2 = readArray(meshInfo, 'uv2Get', 'float[?]', meshInfo.uv2Count * 2)
  local partIndexGlobalToUnique = uintArray(meshInfo.verticesCount)
  local partsMeta = {}
  for _, part in ipairs(parts) do
    if part.skinning then
      local vertCount, indexCount = part.uniqueGlobalCount, part.cornerIndexCount
      partsMeta[#partsMeta + 1] = {name = part.name, uniqueVertCount = vertCount, indexCount = indexCount}
      keyParts[#keyParts + 1] = string.format('part:%s:%d:%d', part.name, vertCount, indexCount)
      payload[#payload + 1] = gatherFloatsByCount(vertices, part.uniqueGlobals, vertCount, 3)
      payload[#payload + 1] = gatherFloatsByCount(normals, part.uniqueGlobals, vertCount, 3)
      payload[#payload + 1] = gatherFloatsByCount(tangents, part.uniqueGlobals, vertCount, 4)
      addStaticBytes(payload, keyParts, uint32sPartIndices(part, partIndexGlobalToUnique))
      addStaticBytes(payload, keyParts, part.skinning)
      addStaticBytes(payload, keyParts, gatherPartCornerFloats(uvs, part, 2))
      addStaticBytes(payload, keyParts, gatherPartCornerFloats(uvs2, part, 2))
      addStaticBytes(payload, keyParts, uint32sPartMaterialIds(part, materialIndexById))
      addStaticBytes(payload, keyParts, uint32sPartSwitchIds(part))
    end
  end

  local propsMeta = {}
  local propInstances = {}
  for _, prop in ipairs(props) do
    local gpuProp = meshInfo:propmeshes(prop.gpuPropIdx)
    local vertCount, indexCount = gpuProp.verticesCount, gpuProp.indicesCount
    propsMeta[#propsMeta + 1] = {name = prop.name, vertCount = vertCount, indexCount = indexCount}
    propInstances[#propInstances + 1] = {pid = prop.pid, bindMatrix = prop.bindMatrix, liveInitMatrix = prop.liveInitMatrix}
    keyParts[#keyParts + 1] = string.format('prop:%s:%d:%d', prop.name, vertCount, indexCount)
    addStaticBytes(payload, keyParts, readFloatBytes(gpuProp, 'verticesGet', vertCount * 3))
    addStaticBytes(payload, keyParts, readFloatBytes(gpuProp, 'normalsGet', vertCount * 3))
    addStaticBytes(payload, keyParts, readFloatBytes(gpuProp, 'tangentsGet', vertCount * 4))
    local indices = readArray(gpuProp, 'indicesGet', 'unsigned int[?]', indexCount)
    addStaticBytes(payload, keyParts, ffi.string(indices, indexCount * 4))
    addStaticBytes(payload, keyParts, gatherFloatsByCount(readArray(gpuProp, 'uv1Get', 'float[?]', gpuProp.uv1Count * 2), indices, indexCount, 2))
    addStaticBytes(payload, keyParts, gatherFloatsByCount(readArray(gpuProp, 'uv2Get', 'float[?]', gpuProp.uv2Count * 2), indices, indexCount, 2))
    addStaticBytes(payload, keyParts, uint32sPropMaterialIds(prop, materialIndexById))
    addStaticBytes(payload, keyParts, uint32sPropSwitchIds(prop))
  end

  return {
    key = hashStringSHA256(table.concat(keyParts)),
    layoutSignature = layoutSignature(veh, hiddenOccupantMeshes),
    nodeIds = nodeIds,
    props = props,
    usedMaterialIds = usedMaterialIds,
    partsMeta = partsMeta,
    propsMeta = propsMeta,
    propInstances = propInstances,
    payload = table.concat(payload),
  }
end

local function onExtensionLoaded()
  FFIHelpers = extensions.BNGLink_FFIHelpers
end

M.onExtensionLoaded = onExtensionLoaded
M.readMeshInfo = readMeshInfo
M.layoutSignature = layoutSignature
M.build = build

return M
