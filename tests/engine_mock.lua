-- Minimal game engine API for running the BNGLink sender outside the game (see lua_sender.py).
-- The scene has two identical vehicles exercising skinning, props, material switches, paints,
-- skins and every dynamic texture kind.

local ffi = require('ffi')
ffi.cdef('typedef struct { uint32_t startIndex; uint32_t indexCount; uint32_t materialId; } gpuPrimitive_t;')
table.clear = require('table.clear')

local H = {time = 0, sent = {}, incoming = {}, logs = {}, luaCommands = {}, loaded = {}, versions = {cef = 1, decal = 1}}
harness = H

local vfs = {}
local function writeFile(path, data) vfs[path] = data end

function log(level, tag, msg) H.logs[#H.logs + 1] = level .. ' ' .. tag .. ' ' .. tostring(msg) end
function nop() end
function clamp(v, lo, hi) return math.min(math.max(v, lo), hi) end
function deepcopy(v)
  if type(v) ~= 'table' then return v end
  local r = {}
  for k, x in pairs(v) do r[k] = deepcopy(x) end
  return r
end
function vec3(x, y, z) return {x = x, y = y, z = z} end
os.clockhp = os.clock
headless_mode = nil

local STOCK_COLORS = {
  InvisibleBlack = {0, 0, 0, 0},
  TransparentWhite = {1, 1, 1, 0},
  Black = {0, 0, 0, 1},
  White = {1, 1, 1, 1},
}
function getStockColor(name) return STOCK_COLORS[name] end

local function encode(v, out)
  local t = type(v)
  if t == 'table' then
    if next(v) == nil or v[1] ~= nil then
      out[#out + 1] = '['
      for i, x in ipairs(v) do
        if i > 1 then out[#out + 1] = ',' end
        encode(x, out)
      end
      out[#out + 1] = ']'
    else
      local keys, values = {}, {}
      for k, x in pairs(v) do
        keys[#keys + 1] = tostring(k)
        values[tostring(k)] = x
      end
      table.sort(keys)
      out[#out + 1] = '{'
      for i, k in ipairs(keys) do
        if i > 1 then out[#out + 1] = ',' end
        encode(k, out)
        out[#out + 1] = ':'
        encode(values[k], out)
      end
      out[#out + 1] = '}'
    end
  elseif t == 'string' then
    out[#out + 1] = '"' .. v:gsub('[%c"\\]', function(c) return string.format('\\u%04x', c:byte()) end) .. '"'
  elseif t == 'number' then
    out[#out + 1] = (v == math.floor(v) and math.abs(v) < 2^53) and string.format('%d', v) or string.format('%.17g', v)
  else
    out[#out + 1] = tostring(v)
  end
end
function jsonEncode(v)
  local out = {}
  encode(v, out)
  return table.concat(out)
end
serialize = jsonEncode

path = {split = function(p)
  local filename = p:match('[^/]*$')
  return p:sub(1, #p - #filename), filename, filename:match('%.([^%.]+)$')
end}
settings = {getValue = function() return nil end}
FS = {
  fileExists = function(_, p) return vfs[p] ~= nil end,
  directoryExists = function() return true end,
  directoryCreate = nop,
  findFiles = function() return {} end,
}
io = {open = function(p)
  local data = vfs[p]
  return data and {read = function() return data end, close = nop}
end}
function convertDDSToPNG(src, dst)
  writeFile(dst, 'png:' .. vfs[src])
  return true
end
function templateRenderMapsPng(json, diffusePath)
  local stem = diffusePath:sub(1, -5)
  writeFile(diffusePath, 'plate:' .. json)
  writeFile(stem .. '_n.png', 'plate_n:' .. json)
  return true
end

package.preload['mime'] = function()
  local chars = 'ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/'
  return {unb64 = function(s)
    local bits = s:gsub('[^%w%+/]', ''):gsub('.', function(c)
      local n, r = chars:find(c, 1, true) - 1, ''
      for i = 6, 1, -1 do r = r .. (math.floor(n / 2 ^ (i - 1)) % 2) end
      return r
    end)
    return (bits:gsub('%d%d%d%d%d%d%d%d', function(b) return string.char(tonumber(b, 2)) end))
  end}
end
package.preload['core/licensePlateDesign'] = function()
  return {loadDesign = function(p) return {format = {}, path = p} end}
end

local socket = {
  send = function(_, data) H.sent[#H.sent + 1] = data end,
  receive = function()
    if #H.incoming == 0 then return nil end
    local res = {}
    for i, datagram in ipairs(H.incoming) do res[i] = {'127.0.0.1', datagram} end
    H.incoming = {}
    return res
  end,
}
function createNetworkServer() return socket end
destroyNetworkServer = nop

local function matrix(c0, c1, c2, pos)
  return {
    getColumn = function(self, i) return ({c0, c1, c2})[i + 1] end,
    getPosition = function() return pos end,
    setPosition = function(_, p) pos = p end,
  }
end

local function fill(buf, values)
  for i, v in ipairs(values) do buf[i - 1] = v end
end

local function primitivesOf(veh, list)
  return function(_, buf)
    for i, p in ipairs(list) do
      local switched = p.msc and veh.switched[p.msc]
      buf[i - 1].startIndex = p.start
      buf[i - 1].indexCount = p.count
      buf[i - 1].materialId = switched or p.mat
    end
  end
end

local function vertexAttribs(count, seed)
  local a = {pos = {}, normal = {}, tangent = {}, uv1 = {}, uv2 = {}}
  for i = 0, count - 1 do
    local s = seed + i
    for _, v in ipairs({s * 0.1, (s * 0.37) % 1, s * -0.05}) do a.pos[#a.pos + 1] = v end
    for _, v in ipairs({0, 0, 1}) do a.normal[#a.normal + 1] = v end
    for _, v in ipairs({1, 0, 0, s % 2 == 0 and 1 or -1}) do a.tangent[#a.tangent + 1] = v end
    for _, v in ipairs({s / 16, 1 - s / 16}) do a.uv1[#a.uv1 + 1] = v end
    for _, v in ipairs({s / 32, s / 64}) do a.uv2[#a.uv2 + 1] = v end
  end
  return a
end

local function meshObject(attribs, count, indices, primitives, veh)
  return {
    verticesCount = count, normalsCount = count, tangentsCount = count, uv1Count = count, uv2Count = count,
    indicesCount = #indices, primitivesCount = #primitives,
    verticesGet = function(_, b) fill(b, attribs.pos) end,
    normalsGet = function(_, b) fill(b, attribs.normal) end,
    tangentsGet = function(_, b) fill(b, attribs.tangent) end,
    uv1Get = function(_, b) fill(b, attribs.uv1) end,
    uv2Get = function(_, b) fill(b, attribs.uv2) end,
    indicesGet = function(_, b) fill(b, indices) end,
    primitivesGet = primitivesOf(veh, primitives),
  }
end

local function gpuMesh(veh)
  local indices = {0, 1, 2, 2, 3, 0, 3, 4, 5, 0, 5, 1, 6, 7, 8, 8, 9, 9, 6, 8, 9, 10, 11, 12}
  local hiddenCount = 3
  if veh.layoutChanged then
    for _, i in ipairs({10, 12, 11}) do indices[#indices + 1] = i end
    hiddenCount = 6
  end
  local info = meshObject(vertexAttribs(13, 0), 13, indices, {}, veh)
  info.flexmeshesCount, info.propmeshesCount = 3, 2
  local flex = {
    {meshName = 'body', prims = {{start = 0, count = 6, mat = 0}, {start = 6, count = 3, mat = 1, msc = 0}, {start = 9, count = 3, mat = 7}}},
    {meshName = 'door.001', prims = {{start = 12, count = 6, mat = 2}, {start = 18, count = 3, mat = 6}}},
    {meshName = 'hidden', prims = {{start = 21, count = hiddenCount, mat = 3}}},
  }
  function info:flexmeshes(i)
    local f = flex[i + 1]
    return {meshName = f.meshName, primitivesCount = #f.prims, primitivesGet = primitivesOf(veh, f.prims)}
  end
  local props = {
    {name = 'mirror.001', count = 4, indices = {0, 1, 2, 2, 3, 0}, prims = {{start = 0, count = 3, mat = 4, msc = 1}, {start = 3, count = 3, mat = 5}}},
    {name = 'ghost', count = 3, indices = {0, 1, 2}, prims = {{start = 0, count = 3, mat = 4}}},
  }
  function info:propmeshes(i)
    local p = props[i + 1]
    local obj = meshObject(vertexAttribs(p.count, 20 + i), p.count, p.indices, p.prims, veh)
    obj.meshName = p.name
    obj.rotation = {getMatrix = function() return matrix(vec3(1, 0, 0), vec3(0, 1, 0), vec3(0, 0, 1), vec3(0, 0, 0)) end}
    obj.position = vec3(0.5, -1, 0.25)
    return obj
  end
  info.dataIsReady = function() return true end
  info.free = nop
  return info
end

GPUMesh = {bng_getGPUMesh = function(id)
  local veh = H.vehicles[id]
  return veh and gpuMesh(veh)
end}

local function flexObject(name, count, offset)
  return {
    getMeshName = function() return name end,
    getVertexCount = function() return count end,
    getVertexLocatorInfo = function(_, i)
      local n = 100 + offset + i
      return {nodes = {n, n + 1, n + 2, i % 2 == 0 and n + 3 or -1}, coords = {x = 0.25, y = 0.5 + i * 0.1, z = -0.1 * i}}
    end,
  }
end

local function material(fields)
  return {
    getClassName = function() return 'Material' end,
    getField = function(_, field, layer) return fields[layer] and fields[layer][field] or '' end,
  }
end

local MATERIAL_NAMES = {'body_mat', 'headlight_off', 'door_mat', 'hidden_mat', 'mirror_mat', nil, 'plate_mat', 'decal_mat', 'headlight_on', 'headlight_on_intense', 'signal_on'}
local materials = {
  body_mat = material({
    [0] = {version = '1.5', activeLayers = '2', baseColorMap = '/vehicles/t/body_b.color.png', normalMap = '/vehicles/t/body_n.png',
      metallicMap = '/vehicles/t/body_m.png', roughnessMap = '/vehicles/t/body_r.png', baseColorFactor = '1 1 1 1',
      metallicFactor = '0.5', roughnessFactor = '0.4', translucentBlendOp = 'None', doubleSided = '1', detailScale = '2 2'},
    [1] = {colorPaletteMap = '/vehicles/t/palette.png', colorPaletteMapUseUV = '1', clearCoatFactor = '1', paletteMetallic = '0',
      opacityFactor = '0.9', baseColorFactor = '0.5 0.5 0.5 1', clearCoatMap = '/vehicles/t/body_cc.png'},
  }),
  headlight_off = material({[0] = {version = '1.5', baseColorMap = '/vehicles/t/light.png', emissiveFactor = '1 1 1',
    emissiveMap = '/vehicles/t/light_e.png', translucent = '1', translucentBlendOp = 'LerpAlpha', alphaTest = '1', alphaRef = '64'}}),
  headlight_on = material({[0] = {version = '1.5', baseColorMap = '/vehicles/t/light.png', emissiveIntensityNits = '100'}}),
  signal_on = material({[0] = {version = '1.5', emissiveFactor = '1 0.5 0'}}),
  door_mat = material({
    [0] = {instanceDiffuse = '1', colorMap = '/vehicles/t/door.jpg', normalMap = '/vehicles/t/door_n.png',
      diffuseColor = '0.8 0.8 0.8 0.5', specular = '1 1 1 1', emissive = '1'},
    [2] = {normalMap = '/vehicles/t/door_n2.png'},
  }),
  ['door_mat.skin_pickup.red'] = material({[0] = {instanceDiffuse = '1', colorMap = '/vehicles/t/door_red.jpg'}}),
  mirror_mat = material({
    [0] = {version = '1.5', activeLayers = '2', baseColorMap = '@screen', opacityMap = '/vehicles/t/mirror_o.png', opacityMapUseUV = '1'},
    [1] = {baseColorMap = '/vehicles/t/overlay.png', diffuseMapUseUV = '1', baseColorFactor = '1 1 1 0.35'},
  }),
  plate_mat = material({[0] = {version = '1.5', baseColorMap = '@licenseplate-default', normalMap = '@licenseplate-default-normal'}}),
  decal_mat = material({[0] = {version = '1.5', baseColorMap = '@DynamicTextureBaseColor', colorPaletteMap = '@DynamicTextureColorPalette',
    metallicMap = '@DynamicTextureMetallic'}}),
}
for _, p in ipairs({'body_b.color.dds', 'body_m.png', 'body_r.png', 'palette.png', 'body_cc.png', 'light.png', 'light_e.png',
    'door.jpg', 'door_n.png', 'door_n2.png', 'door_red.jpg', 'mirror_o.png', 'overlay.png'}) do
  writeFile('/vehicles/t/' .. p, 'file:' .. p)
end
-- Large enough to split the first init into several bulk chunks
writeFile('/vehicles/t/body_n.png', string.rep('n', 100000))

local function vehicleData(veh)
  return {vdata = {
    flexbodies = {{mesh = 'body', fid = 0}, {mesh = 'door', fid = 1}, {mesh = 'hidden', fid = -1}},
    activePartsData = {{slotType = 'skin_pickup', skinName = veh.skin}, {slotType = 'body'}},
    _materials = {
      mv = {lowbeam = 0, highbeam = 0, signal_l_filament = 0},
      triggerList = {'lowbeam', 'highbeam', 'signal_l_filament'},
      triggers = {
        {msc = 0, off = 'headlight_off', on = 'headlight_on', on_intense = 'headlight_on_intense', orgMat = 'headlight_off',
          evalFunctionString = 'return lowbeam + highbeam', materialEmissiveScaling = {on_max = 1}},
        {msc = 1, off = 'mirror_mat', on = 'signal_on', orgMat = 'signal_mat', evalFunctionString = 'return signal_l_filament'},
      },
    },
  }}
end

local function makeVehicle(id)
  local veh = {id = id, switched = {}, skin = 'red', licenseText = 'ABC' .. id, layoutChanged = false,
    color = {x = 0.5, y = 0.2, z = 0.1, w = 1}, colorPalette0 = {x = 1, y = 1, z = 1, w = 1}, colorPalette1 = {x = 0, y = 0, z = 0, w = 0.5},
    paint = {metallic = 0.5, roughness = 0.3, clearcoat = 1, clearcoatRoughness = 0.1}}
  local flexObjs = {flexObject('body', 6, 0), flexObject('door.001', 4, 3)}
  local addedFlexObj = flexObject('trunk', 6, 0)
  local props = {{name = 'antenna'}, {name = 'mirror.002'}}
  function veh:getId() return self.id end
  function veh:getJBeamFilename() return 'mock_vehicle' end
  function veh:isReady() return true end
  function veh:getFlexmeshCount() return #flexObjs + (self.layoutChanged and 1 or 0) end
  function veh:getFlexmesh(i) return flexObjs[i + 1] or addedFlexObj end
  function veh:getPropCount() return #props end
  function veh:getProp(i)
    local t = H.time
    return {
      getMeshName = function() return props[i + 1].name end,
      getLiveTransformWorld = function()
        return matrix(vec3(math.cos(t), math.sin(t), 0), vec3(-math.sin(t), math.cos(t), 0), vec3(0, 0, 1), vec3(id + t, i, 1))
      end,
    }
  end
  function veh:getNodePositionXYZ(nid) return nid * 0.01 + H.time, self.id, nid % 7 end
  function veh:getPositionXYZ() return self.id, H.time, 0.5 end
  function veh:getMaterialNames() return MATERIAL_NAMES end
  function veh:getMaterialIndexByName(name)
    for i = 1, #MATERIAL_NAMES + 1 do
      if MATERIAL_NAMES[i] == name then return i - 1 end
    end
    return -1
  end
  function veh:getMetallicPaintData()
    local p = self.paint
    return {p, {metallic = 0, roughness = 1, clearcoat = 0, clearcoatRoughness = 0}, p}
  end
  function veh:resetMaterials(msc) self.switched[msc] = nil end
  function veh:switchMaterial(msc, name)
    local index = self:getMaterialIndexByName(name)
    self.switched[msc] = index >= 0 and index or 99
  end
  function veh:queueLuaCommand(cmd) H.luaCommands[#H.luaCommands + 1] = {self.id, cmd} end
  function veh:getDynDataFieldbyName(field)
    if field == 'licenseText' then return self.licenseText end
    return nil
  end
  function veh:dumpCEFTexture(tag, out)
    writeFile(out, 'cef:' .. tag .. ':' .. H.versions.cef)
    return true
  end
  function veh:getActiveUITextureTagNames() return {'@screen'} end
  return veh
end

H.vehicles = {[10] = makeVehicle(10), [11] = makeVehicle(11)}
H.vehicleOrder = {10, 11}
H.electrics = {lowbeam = 0, highbeam = 0, signal_l_filament = 0, signal_L = false}
local registeredObjects = {}

be = {
  getObjectCount = function() return #H.vehicleOrder end,
  getObject = function(_, i) return H.vehicles[H.vehicleOrder[i + 1]] end,
  getPlayerVehicleID = function() return H.vehicleOrder[1] end,
}
scenetree = {
  findObjectById = function(id) return H.vehicles[id] end,
  findObject = function(name) return materials[name] or registeredObjects[name] end,
}
function createObject()
  return {registerObject = function(self, name) registeredObjects[name] = self end}
end

local stubs = {
  core_vehicle_manager = {getVehicleData = function(id) return vehicleData(H.vehicles[id]) end},
  core_vehicleBridge = {
    registerValueChangeNotification = nop,
    requestValue = function(_, callback, _, key) callback({result = H.electrics[key]}) end,
    getCachedVehicleData = function(_, key) return H.electrics[key] end,
  },
  core_skiaTemplate = {resolveFormat = function(_, format, text) return {format = format, text = text} end},
  editor_api_dynamicDecals = {
    ready = true,
    exportTextures = function(dir, name)
      for _, suffix in ipairs({'color', 'colorPalette', 'metallic'}) do
        writeFile(dir .. name .. '_' .. suffix .. '.png', 'decal:' .. suffix .. ':' .. H.versions.decal)
      end
      return true
    end,
  },
}
core_vehicle_manager = stubs.core_vehicle_manager
core_vehicles = {
  getModel = function() return {model = {Name = 'Mock Vehicle'}} end,
}

extensions = {}
for name, stub in pairs(stubs) do extensions[name] = stub end

function H.setup(dir)
  function extensions.load(name)
    if extensions[name] then return end
    local module = assert(loadfile(dir .. '/' .. name:gsub('^BNGLink_', '') .. '.lua'))()
    extensions[name] = module
    H.loaded[#H.loaded + 1] = name
    if module.onExtensionLoaded then module.onExtensionLoaded() end
  end
  function extensions.unload(name)
    local module = extensions[name]
    if not module or stubs[name] then return end
    if module.onExtensionUnloaded then module.onExtensionUnloaded() end
    extensions[name] = nil
    for i, loadedName in ipairs(H.loaded) do
      if loadedName == name then table.remove(H.loaded, i) break end
    end
  end
end

function H.hook(name, ...)
  for _, moduleName in ipairs(H.loaded) do
    local fn = extensions[moduleName][name]
    if fn then fn(...) end
  end
end

-- One game frame: GE updates first, then the bulk channel pumps in onPreRender
function H.frame(dt)
  H.time = H.time + dt
  -- Real GE main.lua hooks onUpdate with dtReal, dtSim, dtRaw
  H.hook('onUpdate', dt, dt, dt)
  H.hook('onPreRender', dt)
  for _, entry in ipairs(H.luaCommands) do
    extensions.BNGLink_BNGLink.materialValuesCallback(entry[1], {signal_L = H.electrics.signal_L})
  end
  H.luaCommands = {}
end

function H.push(datagram)
  H.incoming[#H.incoming + 1] = datagram
end

function H.takeSent()
  local sent = H.sent
  H.sent = {}
  return sent
end
