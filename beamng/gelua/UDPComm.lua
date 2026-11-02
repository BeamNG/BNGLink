-- UDP transport to the receiver
-- bulk: large one-shot transfer, chunked, acked per chunk and spread across frames
-- stream, control, update: sent at once without acks; a lost chunk drops the whole transfer

local M = {}

local bit = require('bit')
local max, ceil = math.max, math.ceil

local function parsePort(value)
  local port = tonumber(value)
  return port and port >= 1 and port <= 65535 and port % 1 == 0 and port or nil
end

local socket

local beamngPort = parsePort(os.getenv('BNGLINK_BEAMNG_PORT')) or 6000
local receiverHost = os.getenv('BNGLINK_HOST') or '127.0.0.1'
local receiverPort = parsePort(os.getenv('BNGLINK_RECEIVER_PORT')) or 6001
local MAX_CHUNK = 42000
local BULK_WINDOW = 128
local BULK_BYTES_PER_FRAME = 2 * 1024 * 1024
local MAX_RECEIVE_DATAGRAMS_PER_FRAME = 512
local RESEND_TIMEOUT = 0.1

local KIND_STREAM = 0
local KIND_BULK = 1
local KIND_CONTROL = 2
local KIND_UPDATE = 3
local ACK_MAGIC = 'BNGA'

local transferId = 0
local onReceive = nil

-- One bulk transfer at a time; bulkSentAt holds the send time of every unacked chunk in flight
local bulkBody, bulkId, bulkCount = nil, 0, 0
local bulkNextChunk, bulkAckCount, bulkInFlight = 0, 0, 0
local bulkSentAt = {}
local bulkClock = 0

local function u32le(value)
  return string.char(
    bit.band(value, 0xFF),
    bit.band(bit.rshift(value, 8), 0xFF),
    bit.band(bit.rshift(value, 16), 0xFF),
    bit.band(bit.rshift(value, 24), 0xFF)
  )
end

local function u32leDecode(s, pos)
  local a, b, c, d = s:byte(pos, pos + 3)
  return a + b * 256 + c * 65536 + d * 16777216
end

-- BNG1 magic, kind u8, transfer id u32, chunk u32, chunk count u32
local function packHeader(kind, id, chunk, count)
  return string.char(0x42, 0x4E, 0x47, 0x31, kind) .. u32le(id) .. u32le(chunk) .. u32le(count)
end

local function chunkCount(body)
  return max(1, ceil(#body / MAX_CHUNK))
end

local function chunkData(body, chunk)
  local startPos = chunk * MAX_CHUNK + 1
  return body:sub(startPos, startPos + MAX_CHUNK - 1)
end

local function sendImmediate(kind, payload)
  transferId = transferId + 1
  local count = chunkCount(payload)
  for chunk = 0, count - 1 do
    socket:send(packHeader(kind, transferId, chunk, count) .. chunkData(payload, chunk), receiverHost, receiverPort)
  end
end

local function sendStream(payload)
  sendImmediate(KIND_STREAM, payload)
end

local function sendControl(message)
  sendImmediate(KIND_CONTROL, message)
end

local function sendUpdate(payload, meta)
  local metaJson = jsonEncode(meta)
  sendImmediate(KIND_UPDATE, u32le(#metaJson) .. metaJson .. payload)
end

local function resetBulk(body)
  bulkBody = body
  bulkCount = body and chunkCount(body) or 0
  bulkNextChunk, bulkAckCount, bulkInFlight = 0, 0, 0
  bulkSentAt = {}
end

-- Returns false while the previous bulk transfer is still in progress
local function sendBulk(payload, meta)
  if bulkBody then
    return false
  end

  transferId = transferId + 1
  bulkId = transferId
  local metaJson = jsonEncode(meta)
  resetBulk(u32le(#metaJson) .. metaJson .. payload)
  return true
end

local function cancelBulk()
  resetBulk(nil)
end

-- Returns the number of payload bytes sent
local function sendBulkChunk(chunk)
  local data = chunkData(bulkBody, chunk)
  socket:send(packHeader(KIND_BULK, bulkId, chunk, bulkCount) .. data, receiverHost, receiverPort)
  if not bulkSentAt[chunk] then
    bulkInFlight = bulkInFlight + 1
  end
  bulkSentAt[chunk] = bulkClock
  return #data
end

-- Acks of chunks no longer in flight are duplicates of resent chunks
local function handleAck(datagram)
  if #datagram < 12 then
    return
  end
  local chunk = u32leDecode(datagram, 9)
  if not bulkBody or u32leDecode(datagram, 5) ~= bulkId or not bulkSentAt[chunk] then
    return
  end

  bulkSentAt[chunk] = nil
  bulkInFlight = bulkInFlight - 1
  bulkAckCount = bulkAckCount + 1
  if bulkAckCount >= bulkCount then
    resetBulk(nil)
  end
end

local function pollReceive()
  local res = socket:receive()
  if not res then
    return
  end

  for i, v in ipairs(res) do
    if i > MAX_RECEIVE_DATAGRAMS_PER_FRAME then
      break
    end
    local datagram = v[2]
    if datagram:sub(1, 4) == ACK_MAGIC then
      handleAck(datagram)
    else
      onReceive(datagram)
    end
  end
end

-- Timed out chunks are resent first, then new chunks fill the window, both within the per-frame byte budget
local function onPreRender(dtReal)
  bulkClock = bulkClock + dtReal
  pollReceive()

  if not bulkBody then
    return
  end

  local budget = BULK_BYTES_PER_FRAME
  for chunk, sentAt in pairs(bulkSentAt) do
    if budget <= 0 then
      break
    end
    if bulkClock - sentAt > RESEND_TIMEOUT then
      budget = budget - sendBulkChunk(chunk)
    end
  end
  while budget > 0 and bulkNextChunk < bulkCount and bulkInFlight < BULK_WINDOW do
    budget = budget - sendBulkChunk(bulkNextChunk)
    bulkNextChunk = bulkNextChunk + 1
  end
end

local function setOnReceive(fn)
  onReceive = fn
end

local function getSettings()
  return {beamngPort = beamngPort, receiverHost = receiverHost, receiverPort = receiverPort}
end

local function configure(settings)
  local newBeamngPort, newReceiverPort = parsePort(settings.beamngPort), parsePort(settings.receiverPort)
  local newReceiverHost = settings.receiverHost
  if not (newBeamngPort and newReceiverPort and type(newReceiverHost) == 'string' and newReceiverHost ~= '') then
    return false
  end

  if newBeamngPort ~= beamngPort then
    destroyNetworkServer('udp', beamngPort)
    socket = createNetworkServer('udp', newBeamngPort)
  end
  beamngPort, receiverHost, receiverPort = newBeamngPort, newReceiverHost, newReceiverPort
  resetBulk(nil)
  return true
end

local function onExtensionLoaded()
  socket = createNetworkServer('udp', beamngPort)
end

local function onExtensionUnloaded()
  destroyNetworkServer('udp', beamngPort)
  transferId = 0
  resetBulk(nil)
  onReceive = nil
end

M.onExtensionLoaded = onExtensionLoaded
M.onExtensionUnloaded = onExtensionUnloaded
M.onPreRender = onPreRender
M.sendBulk = sendBulk
M.cancelBulk = cancelBulk
M.sendStream = sendStream
M.sendControl = sendControl
M.sendUpdate = sendUpdate
M.setOnReceive = setOnReceive
M.getSettings = getSettings
M.configure = configure

return M