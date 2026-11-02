-- Stateless helpers turning engine cdata into wire bytes.

local M = {}

local ffi = require('ffi')

local function normalizeMeshName(name)
  return (name:gsub('%.%d%d%d$', ''))
end

-- Exact mesh name first, then the name without its '.001' style suffix
local function findByMeshName(tbl, name)
  return tbl[name] or tbl[normalizeMeshName(name)]
end

-- ctype is a VLA type such as 'float[?]', getter is the engine method filling it
local function readArray(obj, getter, ctype, count)
  local buf = ffi.new(ctype, count)
  obj[getter](obj, buf)
  return buf
end

local function getPrimitives(obj)
  return readArray(obj, 'primitivesGet', 'gpuPrimitive_t[?]', obj.primitivesCount)
end

-- Row-major 3x4: basis vectors as columns, translation in the last column
local function matrixComponents12(m)
  local c0 = m:getColumn(0)
  local c1 = m:getColumn(1)
  local c2 = m:getColumn(2)
  local t = m:getPosition()
  return c0.x, c1.x, c2.x, t.x,
      c0.y, c1.y, c2.y, t.y,
      c0.z, c1.z, c2.z, t.z
end

local function writeMatrix12(buf, base, m)
  buf[base], buf[base + 1], buf[base + 2], buf[base + 3],
  buf[base + 4], buf[base + 5], buf[base + 6], buf[base + 7],
  buf[base + 8], buf[base + 9], buf[base + 10], buf[base + 11] = matrixComponents12(m)
end

local function matrixTo12(m)
  return {matrixComponents12(m)}
end

M.normalizeMeshName = normalizeMeshName
M.findByMeshName = findByMeshName
M.readArray = readArray
M.getPrimitives = getPrimitives
M.writeMatrix12 = writeMatrix12
M.matrixTo12 = matrixTo12

return M
