-- Copyright 2022 xiaobao <xiaobao@linkease.com>
-- Licensed to the public under the MIT License

local http = require "luci.http"
local nixio = require "nixio"
local ltn12 = require "luci.ltn12"
local table = require "table"
local util = require "luci.util"

module("luci.controller.istore_backend", package.seeall)

local BLOCKSIZE = 2048
local ISTOREOS_PORT = 3038

function index()
    entry({"istore"}, call("istore_backend")).leaf=true
end

local function sink_socket(sock, io_err)
  if sock then
    return function(chunk, err)
      if not chunk then
        return 1
      else
        return sock:send(chunk)
      end
    end
  else
    return ltn12.sink.error(io_err or "unable to send socket")
  end
end

local function session_retrieve(sid, allowed_users)
  local sdat = util.ubus("session", "get", { ubus_rpc_session = sid })
  if type(sdat) == "table" and
      type(sdat.values) == "table" and
      type(sdat.values.token) == "string" and
      (not allowed_users or
      util.contains(allowed_users, sdat.values.username))
  then
      return sid, sdat.values
  end
  return nil, nil
end

local function get_session()
  local sid
  local key
  local sdat
  for _, key in ipairs({"sysauth_https", "sysauth_http", "sysauth"}) do
    sid = http.getcookie(key)
    if sid then
      sid, sdat = session_retrieve(sid, nil)
      if sid and sdat then
        return sid, sdat
      end
    end
  end
  return nil, nil
end

local function chunksource(sock, buffer)
        buffer = buffer or ""
        return function()
                local output
                local _, endp, count = buffer:find("^([0-9a-fA-F]+);?.-\r\n")
                while not count and #buffer <= 1024 do
                        local newblock, code = sock:recv(1024 - #buffer)
                        if not newblock then
                                return nil, code
                        end
                        buffer = buffer .. newblock
                        _, endp, count = buffer:find("^([0-9a-fA-F]+);?.-\r\n")
                end
                count = tonumber(count, 16)
                if not count then
                        return nil, -1, "invalid encoding"
                elseif count == 0 then
                        return nil
                elseif count + 2 <= #buffer - endp then
                        output = buffer:sub(endp+1, endp+count)
                        buffer = buffer:sub(endp+count+3)
                        return output
                else
                        output = buffer:sub(endp+1, endp+count)
                        buffer = ""
                        if count - #output > 0 then
                                local remain, code = sock:recvall(count-#output)
                                if not remain then
                                        return nil, code
                                end
                                output = output .. remain
                                count, code = sock:recvall(2)
                        else
                                count, code = sock:recvall(count+2-#buffer+endp)
                        end
                        if not count then
                                return nil, code
                        end
                        return output
                end
        end
end

local function get_cpu_temperature()
  local temp_file = io.open("/sys/class/thermal/thermal_zone0/temp", "r")
  if not temp_file then
    -- coreboot board (no ACPI thermal zone): read coretemp via hwmon.
    -- This Atom's coretemp exposes only temp2..temp5 (no temp1), take the max.
    temp_file = io.popen("for h in /sys/class/hwmon/hwmon*; do [ \"$(cat $h/name 2>/dev/null)\" = coretemp ] && cat $h/temp*_input 2>/dev/null; done | sort -n | tail -n 1")
  end
  if temp_file then
    local temp = temp_file:read("*n")
    temp_file:close()
    if temp then
      return math.floor(temp / 1000)
    end
  end
  return 0
end

function istore_backend()
  -- 检查是否为 system/status 请求
  local uri = http.getenv("REQUEST_URI")
  local is_status_request = string.match(uri, "/istore/system/status/")

  local sock = nixio.connect("127.0.0.1", ISTOREOS_PORT)
  if not sock then
    http.status(500, "connect failed")
    return
  end
  local input = {}
  input[#input+1] = http.getenv("REQUEST_METHOD") .. " " .. http.getenv("REQUEST_URI") .. " HTTP/1.1"
  local req = http.context.request
  local start = "HTTP_"
  local start_len = string.len(start)
  local ctype = http.getenv("CONTENT_TYPE")
  if ctype then
    input[#input+1] = "Content-Type: " .. ctype
  end
  for k, v in pairs(req.message.env) do
    if string.sub(k, 1, start_len) == start and not string.find(k, "FORWARDED") then
      input[#input+1] = string.sub(k, start_len+1, string.len(k)) .. ": " .. v
    end
  end
  local sid, sdat = get_session()
  if sdat ~= nil then
    input[#input+1] = "X-Forwarded-Sid: " .. sid
    input[#input+1] = "X-Forwarded-Token: " .. sdat.token
  end
  -- input[#input+1] = "X-Forwarded-For: " .. http.getenv("REMOTE_HOST") ..":".. http.getenv("REMOTE_PORT")
  local num = tonumber(http.getenv("CONTENT_LENGTH")) or 0
  input[#input+1] = "Content-Length: " .. tostring(num)
  input[#input+1] = "\r\n"
  local source = ltn12.source.cat(ltn12.source.string(table.concat(input, "\r\n")), http.source())
  local ret = ltn12.pump.all(source, sink_socket(sock, "write sock error"))
  if ret ~= 1 then
    sock:close()
    http.status(500, "proxy error")
    return
  end

  local linesrc = sock:linesource()
  local line, code, error = linesrc()
  if not line then
    sock:close()
    http.status(500, "response parse failed")
    return
  end

  local protocol, status, msg = line:match("^([%w./]+) ([0-9]+) (.*)")
  if not protocol then
    sock:close()
    http.status(500, "response protocol error")
    return
  end
  num = tonumber(status) or 0
  http.status(num, msg)

  local chunked = 0
  line = linesrc()
  while line and line ~= "" do
    local key, val = line:match("^([%w-]+)%s?:%s?(.*)")
    if key and key ~= "Status" then
      if key == "Transfer-Encoding" and val == "chunked" then
        chunked = 1
      end
      if key ~= "Connection" and key ~= "Transfer-Encoding" and key ~= "Content-Length" then
        http.header(key, val)
      end
    end
    line = linesrc()
  end
  if not line then
    sock:close()
    http.status(500, "parse header failed")
    return
  end

  local body_buffer = linesrc(true)
  -- S20P dashboard adapter: read-only, exact authenticated GETs only.
  local dashboard_kind
  if sdat ~= nil and num == 200 and http.getenv("REQUEST_METHOD") == "GET" then
    if uri == "/cgi-bin/luci/istore/u/network/status/" then dashboard_kind = "status"
    elseif uri == "/cgi-bin/luci/istore/u/network/statistics/" then dashboard_kind = "statistics"
    elseif uri == "/cgi-bin/luci/istore/network/port/list/" then dashboard_kind = "ports" end
  end
  if dashboard_kind then
    local source
    if chunked == 1 then source = chunksource(sock, body_buffer)
    else source = ltn12.source.cat(ltn12.source.string(body_buffer), sock:blocksource()) end
    local chunks, length, collection_error = {}, 0, nil
    while true do
      local chunk, read_error = source()
      if read_error then collection_error = "cannot collect upstream dashboard response"; break end
      if not chunk then break end
      length = length + #chunk
      if length > 131072 then collection_error = "upstream dashboard response exceeds limit"; break end
      chunks[#chunks+1] = chunk
    end
    local ok, transformed, adapter_error
    if not collection_error then
      ok, transformed, adapter_error = pcall(function()
        local adapter = require(dashboard_kind == "ports" and "luci.model.s20p_ports" or "luci.model.s20p_dashboard")
        return adapter.transform(dashboard_kind, table.concat(chunks), adapter.production_env())
      end)
    end
    if collection_error or not ok or not transformed then
      http.status(503, "Dashboard adapter unavailable")
      http.header("Content-Type", "application/json")
      http.header("Cache-Control", "no-store")
      local json = require "luci.jsonc"
      http.write(json.stringify({error={scope="s20p-dashboard-adapter",retryable=true,
        message=collection_error or (ok and adapter_error) or tostring(transformed)}}))
    else
      http.header("Cache-Control", "no-store")
      http.write(transformed)
    end
    sock:close()
    return
  end
  if chunked == 1 then
    if is_status_request then
      -- 收集所有数据
      local chunks = {}
      local source = chunksource(sock, body_buffer)
      local chunk
      repeat
        chunk = source()
        if chunk then
          table.insert(chunks, chunk)
        end
      until not chunk

      -- 修改 JSON
      local json = table.concat(chunks)
      local cpu_temp = get_cpu_temperature()
      local modified_json = json:gsub('"result":{', string.format('"result":{"cpuTemperature":%d,', cpu_temp))

      -- 发送修改后的数据
      http.write(modified_json)
    else
      ltn12.pump.all(chunksource(sock, body_buffer), http.write)
    end
  else
    if is_status_request then
      -- 收集所有数据
      local chunks = {}
      local body_source = ltn12.source.cat(ltn12.source.string(body_buffer), sock:blocksource())
      local sink = ltn12.sink.table(chunks)
      ltn12.pump.all(body_source, sink)

      -- 修改 JSON
      local json = table.concat(chunks)
      local cpu_temp = get_cpu_temperature()
      local modified_json = json:gsub('"result":{', string.format('"result":{"cpuTemperature":%d,', cpu_temp))

      -- 发送修改后的数据
      http.write(modified_json)
    else
      local body_source = ltn12.source.cat(ltn12.source.string(body_buffer), sock:blocksource())
      ltn12.pump.all(body_source, http.write)
    end
  end

  sock:close()
end
