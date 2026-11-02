-- Init materials: everything that knows the BeamNG material database schema.
-- Scenetree materials, paints, skins and textures -> init meta + texture blob.

local M = {}

local licensePlateDesign = require('core/licensePlateDesign')
local mime = require('mime')

local logTag = 'BNGLink'

local DYNAMIC_TEXTURE_SUFFIX = {
  ['@DynamicTextureBaseColor'] = 'color',
  ['@DynamicTextureColorPalette'] = 'colorPalette',
  ['@DynamicTextureMetallic'] = 'metallic',
  ['@DynamicTextureRoughness'] = 'roughness',
}
local DYNAMIC_TEXTURE_DIR = '/temp/bnglinkDecals/'
local DYNAMIC_TEXTURE_NAME = 'export'
-- 1x1 PNG sent for CEF textures that cannot be dumped yet, so the receiver still gets a texture slot to update
local DYNAMIC_TEXTURE_PLACEHOLDER = mime.unb64(
  'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR4nGNgYGD4DwABBAEAX+XDSwAAAABJRU5ErkJggg=='
)
local TEXTURE_COOKER_SUFFIXES = {'%.color%.png$', '%.normal%.png$', '%.data%.png$'}
local PLATE_MAP_SUFFIXES = {['-normal'] = '_n', ['-specular'] = '_s', ['-metallic'] = '_m'}
local DEFAULT_LICENSE_PLATE_DESIGN = 'vehicles/common/licenseplates/default/licensePlate-default.sktemplate.json'
local PAINT_SCALAR_FIELDS = {'metallic', 'roughness', 'clearcoat', 'clearcoatRoughness'}
local PAINT_EPSILON = 0.000001
local LAYER_TEXTURES = {
  {'normalTexture', 'normalMap', 'normalMapUseUV'},
  {'metallicTexture', 'metallicMap', 'metallicMapUseUV'},
  {'roughnessTexture', 'roughnessMap', 'roughnessMapUseUV'},
  {'aoTexture', 'ambientOcclusionMap', 'ambientOcclusionMapUseUV'},
  {'opacityTexture', 'opacityMap', 'opacityMapUseUV'},
  {'overlayTexture', 'overlayMap', 'overlayMapUseUV'},
  {'specularTexture', 'specularMap', 'specularMapUseUV'},
  {'reflectivityTexture', 'reflectivityMap', 'reflectivityMapUseUV'},
  {'emissiveTexture', 'emissiveMap', 'emissiveMapUseUV'},
  {'clearCoatTexture', 'clearCoatMap', 'clearCoatMapUseUV'},
  {'clearCoatRoughnessTexture', 'clearCoatRoughnessMap', 'clearCoatMapUseUV'},
  {'detailBaseColorTexture', 'detailMap', 'detailMapUseUV'},
  {'detailMetallicTexture', 'metallicDetailMap', 'detailMapUseUV'},
  {'detailRoughnessTexture', 'roughnessDetailMap', 'detailMapUseUV'},
  {'detailAoTexture', 'ambientOcclusionDetailMap', 'detailMapUseUV'},
  {'detailOpacityTexture', 'opacityDetailMap', 'opacityDetailMapUseUV'},
  {'detailNormalTexture', 'detailNormalMap', 'normalDetailMapUseUV'},
}

-- Dynamic decals are exported once per init/update, plate renders once per vehicle and format
local dynamicDecalsExported = false
local plateTexturePaths = {}

local function mimeType(ext)
  return (ext == 'jpg' or ext == 'jpeg') and 'image/jpeg' or 'image/png'
end

local function isTextureCookerFilepath(filepath)
  for _, pattern in ipairs(TEXTURE_COOKER_SUFFIXES) do
    if filepath:match(pattern) then
      return true
    end
  end
  return false
end

local function resolveDynamicDecalTexture(filepath)
  local api = extensions.editor_api_dynamicDecals
  if not api or not api.ready then
    return nil
  end

  if not dynamicDecalsExported then
    if not FS:directoryExists(DYNAMIC_TEXTURE_DIR) then
      FS:directoryCreate(DYNAMIC_TEXTURE_DIR, true)
    end
    if not api.exportTextures(DYNAMIC_TEXTURE_DIR, DYNAMIC_TEXTURE_NAME, 'png') then
      return nil
    end
    dynamicDecalsExported = true
  end

  local outPath = DYNAMIC_TEXTURE_DIR .. DYNAMIC_TEXTURE_NAME .. '_' .. DYNAMIC_TEXTURE_SUFFIX[filepath] .. '.png'
  return FS:fileExists(outPath) and outPath or nil
end

-- '@licenseplate-<format>[-normal|-specular|-metallic]' -> format, rendered map suffix
local function plateTextureInfo(filepath)
  local format = filepath:match('^@licenseplate%-(.+)$')
  if not format then
    return nil
  end
  local mapSuffix = ''
  for suffix, map in pairs(PLATE_MAP_SUFFIXES) do
    if format:sub(-#suffix) == suffix then
      format, mapSuffix = format:sub(1, -#suffix - 1), map
      break
    end
  end
  return format == 'default' and '30-15' or format, mapSuffix
end

local function invalidatePlateTextures(vehicleId)
  local prefix = tostring(vehicleId) .. ':'
  for key in pairs(plateTexturePaths) do
    if key:sub(1, #prefix) == prefix then
      plateTexturePaths[key] = nil
    end
  end
end

local function invalidateDecalExport()
  dynamicDecalsExported = false
end

local function resetTextureCaches()
  dynamicDecalsExported = false
  plateTexturePaths = {}
end

local function resolvePlateTexture(filepath, veh)
  local format, mapSuffix = plateTextureInfo(filepath)
  if not format then
    return nil
  end
  local key = tostring(veh:getId()) .. ':' .. format
  local paths = plateTexturePaths[key]
  if not paths then
    local designPath = veh:getDynDataFieldbyName('licenseDesign', 0) or ''
    local text = veh:getDynDataFieldbyName('licenseText', 0) or 'BEAMNG'
    local skipGeneration = settings.getValue('SkipGenerateLicencePlate')
      or veh:getDynDataFieldbyName('licenseNoGen', 0)
      or headless_mode
    if skipGeneration then
      designPath = DEFAULT_LICENSE_PLATE_DESIGN
      text = 'BEAMNG'
    end

    local design = licensePlateDesign.loadDesign(designPath)
    if not (design and design.format) then
      designPath = DEFAULT_LICENSE_PLATE_DESIGN
      design = licensePlateDesign.loadDesign(designPath)
    end

    local resolveFormat = extensions.core_skiaTemplate.resolveFormat
    local resolved = resolveFormat(design, format, text, veh)
    if not resolved and designPath ~= DEFAULT_LICENSE_PLATE_DESIGN then
      resolved = resolveFormat(licensePlateDesign.loadDesign(DEFAULT_LICENSE_PLATE_DESIGN), format, text, veh)
    end
    if not resolved then
      return nil
    end

    local stem = '/temp/bnglink_licenseplate_' .. tostring(veh:getId()) .. '_' .. format:gsub('[^%w_-]', '_')
    if not templateRenderMapsPng(jsonEncode(resolved), stem .. '.png') then
      return nil
    end
    paths = {[''] = stem .. '.png', _n = stem .. '_n.png', _s = stem .. '_s.png', _m = stem .. '_m.png'}
    plateTexturePaths[key] = paths
  end

  local path = paths[mapSuffix]
  return FS:fileExists(path) and path or nil
end

local function dynamicTextureKind(filepath)
  if DYNAMIC_TEXTURE_SUFFIX[filepath] then
    return 'decal'
  end
  if filepath:match('^@licenseplate%-') then
    return 'plate'
  end
  return filepath:sub(1, 1) == '@' and 'cef' or nil
end

local function resolveTexturePath(filepath, veh, dynamicKind)
  if FS:fileExists(filepath) then
    return filepath
  end
  if dynamicKind == 'decal' then
    return resolveDynamicDecalTexture(filepath)
  end
  if dynamicKind == 'plate' then
    return resolvePlateTexture(filepath, veh)
  end
  if dynamicKind == 'cef' then
    local outPath = '/temp/ceftexture_' .. filepath:gsub('@', '') .. '.png'
    return veh:dumpCEFTexture(filepath, outPath) and outPath or nil
  end
  if isTextureCookerFilepath(filepath) then
    return filepath:sub(1, -5) .. '.dds'
  end
  local found = FS:findFiles('/', filepath, -1, false, false)
  return #found == 1 and found[1] or nil
end

-- textureByPath is an optional per-init cache, false marks paths that failed to resolve
local function readTextureEntry(filepath, veh, textureByPath)
  if textureByPath and textureByPath[filepath] ~= nil then
    return textureByPath[filepath]
  end

  local dynamicKind = dynamicTextureKind(filepath)
  local resolved = resolveTexturePath(filepath, veh, dynamicKind)
  if not resolved then
    if textureByPath then
      textureByPath[filepath] = false
    end
    return nil
  end

  local _, filename, ext = path.split(resolved)
  local readPath = resolved
  if ext == 'dds' then
    readPath = '/temp/' .. resolved .. '.png'
    if not FS:fileExists(readPath) and not convertDDSToPNG(resolved, readPath) then
      return nil
    end
  end

  local f = io.open(readPath, 'rb')
  if not f then
    return nil
  end
  local entry = {data = f:read('*all'), mime = mimeType(ext), name = filename, source = filepath, dynamicKind = dynamicKind}
  f:close()
  if textureByPath then
    textureByPath[filepath] = entry
  end
  return entry
end

-- Adds the texture to the init texture list once and stores its list index in def[field].
-- Textures the receiver already has are listed without their data.
local function addTextureRef(ctx, def, field, filepath)
  if filepath == '' then
    return
  end
  local idx = ctx.textureIndexByPath[filepath]
  if not idx then
    if ctx.textureByPath[filepath] == false then
      return
    end
    local dynamicKind = dynamicTextureKind(filepath)
    local key = dynamicKind and (tostring(ctx.veh:getId()) .. ':' .. filepath) or filepath
    local entry
    if not dynamicKind and ctx.deliveredKeys[key] then
      local _, filename, ext = path.split(filepath)
      entry = {mime = mimeType(ext), name = filename, source = filepath}
      ctx.textureByPath[filepath] = entry
    else
      entry = readTextureEntry(filepath, ctx.veh, ctx.textureByPath)
    end
    if not entry and dynamicKind == 'cef' then
      entry = {data = DYNAMIC_TEXTURE_PLACEHOLDER, mime = 'image/png', name = filepath:sub(2) .. '.png', source = filepath}
      ctx.textureByPath[filepath] = entry
    end
    if not entry then
      log('W', logTag, 'Could not export material texture: ' .. filepath)
      return
    end
    entry.key = key
    entry.dynamicKind = dynamicKind
    idx = #ctx.textureList
    ctx.textureList[idx + 1] = entry
    ctx.textureIndexByPath[filepath] = idx
  end
  def[field] = idx
end

local function flag(mat, field, layer)
  return mat:getField(field, layer) == '1'
end

local function addMaterialTexture(ctx, target, mat, field, mapField, layer, uvField, onlyWithTexture)
  addTextureRef(ctx, target, field, mat:getField(mapField, layer))
  if uvField and (not onlyWithTexture or target[field] ~= nil) then
    target[field:gsub('Texture$', '') .. 'UseUV1'] = flag(mat, uvField, layer)
  end
end

local function number(mat, field, layer)
  return tonumber(mat:getField(field, layer))
end

local function activeLayerCount(mat)
  return math.max(1, math.min(4, tonumber(mat:getField('activeLayers', 0)) or 1))
end

local function findLayer(mat, field)
  for layer = 0, 3 do
    if mat:getField(field, layer) ~= '' then
      return layer
    end
  end
  return nil
end

local function firstStageField(mat, field)
  local layer = findLayer(mat, field)
  return layer and mat:getField(field, layer) or ''
end

local function parseFloats(str)
  local values = {}
  for token in str:gmatch('%S+') do
    values[#values + 1] = tonumber(token)
  end
  if #values == 0 then -- invisible (e.g. dashboard icons)
    return str ~= '' and getStockColor(str) or nil
  end
  return values
end

local function srgbToLinear(values)
  if values then
    for i = 1, math.min(3, #values) do
      values[i] = values[i] >= 0 and values[i] ^ 2.2 or 0
    end
  end
  return values
end

local function linearColor(mat, field, layer)
  return srgbToLinear(parseFloats(mat:getField(field, layer)))
end

-- Palette flags default to on when the field is not set
local function paletteFlag(mat, field, layer)
  local value = mat:getField(field, layer)
  return value == '' or value == '1'
end

local function buildPaints(veh)
  local metallic = veh:getMetallicPaintData()
  local colors = {veh.color, veh.colorPalette0, veh.colorPalette1}
  local paints = {}
  for i = 1, 3 do
    local c = colors[i]
    local paint = {baseColor = srgbToLinear({c.x, c.y, c.z, c.w})}
    for _, field in ipairs(PAINT_SCALAR_FIELDS) do
      paint[field] = metallic[i][field]
    end
    paints[i] = paint
  end
  return paints
end

local function paintsChanged(a, b)
  for i = 1, 3 do
    for j = 1, 4 do
      if math.abs(a[i].baseColor[j] - b[i].baseColor[j]) > PAINT_EPSILON then
        return true
      end
    end
    for _, field in ipairs(PAINT_SCALAR_FIELDS) do
      if math.abs(a[i][field] - b[i][field]) > PAINT_EPSILON then
        return true
      end
    end
  end
  return false
end

local function isPaintedMaterial(mat)
  for layer = 0, 3 do
    if flag(mat, 'instanceDiffuse', layer) or mat:getField('colorPaletteMap', layer) ~= '' then
      return true
    end
  end
  return false
end

-- Returns paintLayer itself when the paint layer is the only active layer
local function findSubstrateLayer(mat, paintLayer)
  local activeLayers = activeLayerCount(mat)
  for layer = 0, activeLayers - 1 do
    if layer ~= paintLayer and (mat:getField('metallicMap', layer) ~= '' or mat:getField('baseColorMap', layer) ~= ''
        or mat:getField('roughnessMap', layer) ~= '') then
      return layer
    end
  end
  return (paintLayer == 0 and activeLayers > 1) and 1 or 0
end

local function findSkinOverlayLayer(mat, substrateLayer)
  for layer = 0, 3 do
    if layer ~= substrateLayer and mat:getField('baseColorMap', layer) ~= '' then
      return layer
    end
  end
  return nil
end

local function buildPaintDef(ctx, mat, substrateLayer)
  local paletteLayer = findLayer(mat, 'colorPaletteMap')
  if paletteLayer then
    local paletteOnTop = paletteLayer >= substrateLayer
    local opacityLayer = paletteOnTop and paletteLayer or substrateLayer
    local paint = {
      mode = 'palette',
      paletteOnTop = paletteOnTop,
      paletteUseUV1 = flag(mat, 'colorPaletteMapUseUV', paletteLayer),
      diffuseUseUV1 = flag(mat, 'diffuseMapUseUV', paletteLayer),
      clearCoatUseUV1 = flag(mat, 'clearCoatMapUseUV', paletteLayer),
      clearCoatFactor = number(mat, 'clearCoatFactor', paletteLayer),
      clearCoatRoughnessFactor = number(mat, 'clearCoatRoughnessFactor', paletteLayer),
      baseColorFactor = linearColor(mat, 'baseColorFactor', paletteLayer),
      opacityFactor = number(mat, 'opacityFactor', opacityLayer),
      paletteBaseColor = paletteFlag(mat, 'paletteBaseColor', paletteLayer),
      paletteMetallic = paletteFlag(mat, 'paletteMetallic', paletteLayer),
      paletteRoughness = paletteFlag(mat, 'paletteRoughness', paletteLayer),
      paletteClearCoat = paletteFlag(mat, 'paletteClearCoat', paletteLayer),
      paletteClearCoatRoughness = paletteFlag(mat, 'paletteClearCoatRoughness', paletteLayer),
      paintMetallicFactor = number(mat, 'metallicFactor', paletteLayer),
      paintRoughnessFactor = number(mat, 'roughnessFactor', paletteLayer),
      firstOverlayLayer = math.max(paletteLayer, substrateLayer) + 1,
    }
    addTextureRef(ctx, paint, 'paletteTexture', mat:getField('colorPaletteMap', paletteLayer))
    addTextureRef(ctx, paint, 'colorMapTexture', mat:getField('baseColorMap', paletteLayer))
    addTextureRef(ctx, paint, 'opacityTexture', mat:getField('opacityMap', opacityLayer))
    if paint.opacityTexture then
      paint.opacityUseUV1 = flag(mat, 'opacityMapUseUV', opacityLayer)
    end
    addTextureRef(ctx, paint, 'clearCoatTexture', mat:getField('clearCoatMap', paletteLayer))
    return paint
  end

  local instanceLayer = findLayer(mat, 'instanceDiffuse')
  if not instanceLayer then
    return nil
  end
  local paint = {mode = 'instance', diffuseUseUV1 = flag(mat, 'diffuseMapUseUV', instanceLayer)}
  local colorField = mat:getField('version', 0) == '1.5' and 'baseColorMap' or 'colorMap'
  addTextureRef(ctx, paint, 'colorMapTexture', mat:getField(colorField, instanceLayer))
  return paint
end

local function buildMaterialLayers(ctx, mat, pbr15)
  local layers = {}
  for layerIndex = 0, activeLayerCount(mat) - 1 do
    local baseColorFactor = linearColor(mat, pbr15 and 'baseColorFactor' or 'diffuseColor', layerIndex)
    local opacityFactor = number(mat, 'opacityFactor', layerIndex)
    if not pbr15 and baseColorFactor and baseColorFactor[4] then
      opacityFactor = (opacityFactor or 1) * baseColorFactor[4]
    end
    local layer = {
      baseColorFactor = baseColorFactor,
      metallicFactor = number(mat, 'metallicFactor', layerIndex),
      roughnessFactor = number(mat, 'roughnessFactor', layerIndex),
      opacityFactor = opacityFactor,
      specularFactor = parseFloats(mat:getField('specular', layerIndex)),
      reflectivityFactor = number(mat, 'reflectivityMapFactor', layerIndex),
      anisotropy = flag(mat, 'useAnisotropic', layerIndex) and 1 or 0,
      legacyEmissive = not pbr15 and flag(mat, 'emissive', layerIndex),
      normalStrength = number(mat, 'normalMapStrength', layerIndex),
      emissiveFactor = parseFloats(mat:getField('emissiveFactor', layerIndex)),
      emissiveIntensity = number(mat, 'emissiveIntensityNits', layerIndex),
      clearCoatFactor = number(mat, 'clearCoatFactor', layerIndex),
      clearCoatRoughnessFactor = number(mat, 'clearCoatRoughnessFactor', layerIndex),
      detailScale = parseFloats(mat:getField('detailScale', layerIndex)),
      detailBaseColorStrength = number(mat, 'detailBaseColorMapStrength', layerIndex),
      detailMetallicStrength = number(mat, 'detailMetallicMapStrength', layerIndex),
      detailRoughnessStrength = number(mat, 'detailRoughnessMapStrength', layerIndex),
      detailAoStrength = number(mat, 'detailAoMapStrength', layerIndex),
      detailOpacityStrength = number(mat, 'detailOpacityMapStrength', layerIndex),
      detailNormalStrength = number(mat, 'detailNormalMapStrength', layerIndex),
    }

    addMaterialTexture(ctx, layer, mat, 'baseColorTexture', pbr15 and 'baseColorMap' or 'colorMap', layerIndex, 'diffuseMapUseUV', true)
    for _, entry in ipairs(LAYER_TEXTURES) do
      addMaterialTexture(ctx, layer, mat, entry[1], entry[2], layerIndex, entry[3], true)
    end
    layers[#layers + 1] = layer
  end

  return layers
end

local function buildMaterialDef(ctx, name, mat)
  local painted = isPaintedMaterial(mat)
  local pbr15 = mat:getField('version', 0) == '1.5'
  local def = {name = name, pbr15 = pbr15, layers = buildMaterialLayers(ctx, mat, pbr15)}
  local paintLayer = findLayer(mat, 'colorPaletteMap') or findLayer(mat, 'instanceDiffuse')
  local substrateLayer = painted and findSubstrateLayer(mat, paintLayer) or 0

  if pbr15 then
    local normalLayer = mat:getField('normalMap', substrateLayer) ~= '' and substrateLayer or paintLayer or 0
    addMaterialTexture(ctx, def, mat, 'normalTexture', 'normalMap', normalLayer, 'normalMapUseUV')
    addMaterialTexture(ctx, def, mat, 'metallicTexture', 'metallicMap', substrateLayer, 'metallicMapUseUV')
    addMaterialTexture(ctx, def, mat, 'roughnessTexture', 'roughnessMap', substrateLayer, 'roughnessMapUseUV')
    local emissiveLayer = findLayer(mat, 'emissiveMap')
    if emissiveLayer then
      addMaterialTexture(ctx, def, mat, 'emissiveTexture', 'emissiveMap', emissiveLayer, 'emissiveMapUseUV')
    end
    def.metallicFactor = number(mat, 'metallicFactor', substrateLayer)
    def.roughnessFactor = number(mat, 'roughnessFactor', substrateLayer)
    def.emissiveFactor = parseFloats(firstStageField(mat, 'emissiveFactor'))
    if not painted then
      addMaterialTexture(ctx, def, mat, 'baseColorTexture', 'baseColorMap', substrateLayer, 'diffuseMapUseUV')
      addMaterialTexture(ctx, def, mat, 'opacityTexture', 'opacityMap', substrateLayer, 'opacityMapUseUV')
      local opacityLayer = findLayer(mat, 'opacityMap')
      if not def.opacityTexture and opacityLayer then
        addMaterialTexture(ctx, def, mat, 'opacityTexture', 'opacityMap', opacityLayer, 'opacityMapUseUV')
      end
      def.baseColorFactor = linearColor(mat, 'baseColorFactor', substrateLayer)
        or srgbToLinear(parseFloats(firstStageField(mat, 'baseColorFactor')))
      local skinLayer = findSkinOverlayLayer(mat, substrateLayer)
      if skinLayer then
        local colorUseUV1 = flag(mat, 'diffuseMapUseUV', skinLayer)
        local opacityUseUV = mat:getField('opacityMapUseUV', skinLayer)
        local skin = {colorUseUV1 = colorUseUV1, opacityUseUV1 = colorUseUV1}
        if opacityUseUV ~= '' then
          skin.opacityUseUV1 = opacityUseUV == '1'
        end
        addTextureRef(ctx, skin, 'colorTexture', mat:getField('baseColorMap', skinLayer))
        addTextureRef(ctx, skin, 'opacityTexture', mat:getField('opacityMap', skinLayer))
        if skin.colorTexture then
          def.skinOverlay = skin
        end
      end
    end
  else
    addMaterialTexture(ctx, def, mat, 'normalTexture', 'normalMap', 0)
    def.roughnessFactor = tonumber(firstStageField(mat, 'roughnessFactor'))
    if not painted then
      addMaterialTexture(ctx, def, mat, 'baseColorTexture', 'colorMap', mat:getField('normalMap', 2) ~= '' and 1 or 0)
    end
  end

  if painted then
    def.paint = buildPaintDef(ctx, mat, substrateLayer)
    if not def.baseColorTexture then
      addMaterialTexture(ctx, def, mat, 'baseColorTexture', 'baseColorMap', substrateLayer, 'diffuseMapUseUV')
    end
  end

  local blendOp = mat:getField('translucentBlendOp', 0)
  def.blendOp = blendOp
  def.transparent = flag(mat, 'translucent', 0) and blendOp ~= 'None'
  def.zWrite = flag(mat, 'translucentZWrite', 0)
  def.alphaTest = flag(mat, 'alphaTest', 0)
  def.alphaRef = number(mat, 'alphaRef', 0) or 0
  def.opacityFactor = number(mat, 'opacityFactor', substrateLayer) or tonumber(firstStageField(mat, 'opacityFactor'))
  def.doubleSided = flag(mat, 'doubleSided', 0)
  return def
end

local function isSkinSlot(slotType)
  return type(slotType) == 'string' and (slotType:find('skin_') or slotType == 'paint_design')
end

local function partUsesSkin(part)
  if type(part.slotType) == 'table' then
    for _, value in ipairs(part.slotType) do
      if isSkinSlot(value) then
        return true
      end
    end
    return false
  end
  return isSkinSlot(part.slotType)
end

-- Sorted "slot.skin" names joined with '|', or nil when the vehicle data is not available
local function getActiveSkinName(vehicleId)
  local data = core_vehicle_manager.getVehicleData(vehicleId)
  if not data then
    return nil
  end

  local skinNames = {}
  for _, part in pairs(data.vdata.activePartsData) do
    if partUsesSkin(part) then
      local skinSlot = type(part.skinType) == 'string' and part.skinType ~= '' and part.skinType or part.slotType
      if skinSlot == 'paint_design' or type(skinSlot) ~= 'string' then
        skinSlot = ''
      end
      local skinName = part.skinName or part.globalSkin or ''
      skinNames[#skinNames + 1] = skinSlot ~= '' and (skinSlot .. '.' .. skinName) or skinName
    end
  end

  table.sort(skinNames)
  return table.concat(skinNames, '|')
end

local function findMaterial(matName, skinName)
  for candidate in skinName:gmatch('[^|]+') do
    for _, fullName in ipairs({matName .. '.' .. candidate, matName .. '.skin.' .. candidate}) do
      local skinMat = scenetree.findObject(fullName)
      if skinMat and skinMat:getClassName():lower() == 'material' then
        return skinMat, fullName
      end
    end
  end
  local mat = scenetree.findObject(matName)
  return mat and mat:getClassName():lower() == 'material' and mat or nil, matName
end

local function fallbackMaterialDef(name)
  return {
    name = name,
    transparent = false,
    zWrite = false,
    alphaTest = false,
    alphaRef = 0,
    doubleSided = false,
    layers = {{baseColorFactor = {1, 1, 1, 1}, metallicFactor = 0, roughnessFactor = 0.5, opacityFactor = 1}},
    baseColorFactor = {1, 1, 1, 1},
    metallicFactor = 0,
    roughnessFactor = 0.5,
  }
end

local function appendMaterial(materials, materialIdBySourceName, def, sourceName)
  def.id = #materials
  def.sourceName = sourceName
  materials[#materials + 1] = def
  materialIdBySourceName[sourceName] = def.id
end

local function exportMaterial(ctx, materials, materialIdBySourceName, activeSkinName, sourceName, missingMessage)
  local mat, exportName = findMaterial(sourceName, activeSkinName or '')
  if mat then
    appendMaterial(materials, materialIdBySourceName, buildMaterialDef(ctx, exportName, mat), sourceName)
  else
    log('W', logTag, string.format(missingMessage, ctx.veh:getId(), sourceName))
    appendMaterial(materials, materialIdBySourceName, fallbackMaterialDef(sourceName), sourceName)
  end
end

-- Exports usedMaterialIds in order, then appends the on/off variants of material switch triggers.
-- Returns materials, textureList, activeSkinName, materialIdBySourceName.
local function build(veh, usedMaterialIds, materialData, deliveredKeys)
  local ctx = {veh = veh, deliveredKeys = deliveredKeys, textureList = {}, textureByPath = {}, textureIndexByPath = {}}
  local materials = {}
  local materialIdBySourceName = {}
  local activeSkinName = getActiveSkinName(veh:getId())

  local matNames = veh:getMaterialNames()
  for _, matId in ipairs(usedMaterialIds) do
    local matName = matNames[matId + 1]
    if matName and matName ~= '' then
      exportMaterial(ctx, materials, materialIdBySourceName, activeSkinName, matName,
        'Vehicle %d material %s was not found, using fallback')
    else
      log('W', logTag, string.format('Vehicle %d material id %d has no material name, using fallback', veh:getId(), matId))
      local name = 'missing_material_' .. tostring(matId)
      appendMaterial(materials, materialIdBySourceName, fallbackMaterialDef(name), name)
    end
  end

  for _, trigger in ipairs(materialData.triggers) do
    for _, field in ipairs({'off', 'on', 'on_intense'}) do
      local variant = trigger[field]
      if variant and variant ~= '' and not materialIdBySourceName[variant] then
        exportMaterial(ctx, materials, materialIdBySourceName, activeSkinName, variant,
          'Vehicle %d material variant %s was not found, using fallback')
      end
    end
  end
  local damageVariants = {}
  for variant in pairs(materialData.damageVariants or {}) do
    damageVariants[#damageVariants + 1] = variant
  end
  table.sort(damageVariants)
  for _, variant in ipairs(damageVariants) do
    if variant and variant ~= '' and not materialIdBySourceName[variant] then
      exportMaterial(ctx, materials, materialIdBySourceName, activeSkinName, variant,
        'Vehicle %d damaged material variant %s was not found, using fallback')
    end
  end

  return materials, ctx.textureList, activeSkinName, materialIdBySourceName
end

-- Appends the data of every texture the receiver does not have yet (dynamic textures are always sent)
local function appendTextureBlob(buffer, textureList, deliveredKeys)
  local payloadParts = {buffer}
  local descriptors = {}
  local offset = 0
  for i, entry in ipairs(textureList) do
    local descriptor = {mime = entry.mime, name = entry.name, key = entry.key, dynamicKind = entry.dynamicKind}
    if entry.dynamicKind or not deliveredKeys[entry.key] then
      descriptor.offset = offset
      descriptor.size = #entry.data
      payloadParts[#payloadParts + 1] = entry.data
      offset = offset + #entry.data
    end
    descriptors[i] = descriptor
  end
  return table.concat(payloadParts), descriptors
end

M.build = build
M.appendTextureBlob = appendTextureBlob
M.buildPaints = buildPaints
M.paintsChanged = paintsChanged
M.getActiveSkinName = getActiveSkinName
M.readTextureEntry = readTextureEntry
M.invalidatePlateTextures = invalidatePlateTextures
M.invalidateDecalExport = invalidateDecalExport
M.resetTextureCaches = resetTextureCaches

return M
