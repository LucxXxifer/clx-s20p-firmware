-- Narrow read-only dashboard adapter for kernel-managed side-router uplinks.
-- Lua 5.1 compatible; production I/O is isolated so calculations can be tested.
local M = {}
local MAX_ITEMS, MAX_INTERVAL = 12, 30

local function finite(value)
  return type(value) == "number" and value == value and value ~= math.huge and value ~= -math.huge
end

local function integer(value)
  return finite(value) and value >= 0 and value <= 9007199254740991 and value % 1 == 0
end

local function device_name(value)
  return type(value) == "string" and #value > 0 and #value <= 15 and value:match("^[%w_.:-]+$") ~= nil
end

local function ipv4(value)
  if type(value) ~= "string" then return false end
  local a,b,c,d = value:match("^(%d+)%.(%d+)%.(%d+)%.(%d+)$")
  return a and tonumber(a) <= 255 and tonumber(b) <= 255 and tonumber(c) <= 255 and tonumber(d) <= 255
end

local function copy(value)
  if type(value) ~= "table" then return value end
  local result = {}
  for key, item in pairs(value) do result[key] = copy(item) end
  return result
end

function M.eligible(uri, method, authenticated, status)
  if method ~= "GET" or authenticated ~= true or status ~= 200 then return nil end
  if uri == "/cgi-bin/luci/istore/u/network/status/" then return "status" end
  if uri == "/cgi-bin/luci/istore/u/network/statistics/" then return "statistics" end
  return nil
end

local function select_uplink(env)
  local routes, route_error = env.routes()
  if type(routes) ~= "table" then return nil, route_error or "cannot read main IPv4 routes" end
  local candidates, metric = {}, nil
  for _, route in ipairs(routes) do
    local kind = route.type or "unicast"
    local main_table = route.table == nil or route.table == "main" or route.table == 254
    if (route.dst == "default" or route.dst == "0.0.0.0/0") and kind == "unicast" and main_table then
      local cost = route.metric == nil and 0 or tonumber(route.metric)
      if not finite(cost) or cost < 0 then return nil, "invalid default route metric" end
      if metric == nil or cost < metric then candidates, metric = {route}, cost
      elseif cost == metric then candidates[#candidates+1] = route
      end
    end
  end
  if #candidates == 0 then return nil, "no main IPv4 default route" end
  local chosen = candidates[1]
  for _, route in ipairs(candidates) do
    if route.nexthops or route.multipath then return nil, "multipath default route is unsupported" end
    if not device_name(route.dev) or (route.gateway ~= nil and not ipv4(route.gateway)) then
      return nil, "default route device or gateway is invalid"
    end
    if chosen.dev ~= route.dev or chosen.gateway ~= route.gateway or chosen.prefsrc ~= route.prefsrc then
      return nil, "ambiguous equal-cost default routes"
    end
  end
  local interfaces, interface_error = env.interfaces()
  if type(interfaces) ~= "table" then return nil, interface_error or "cannot read netifd interfaces" end
  local selected, address
  for _, interface in ipairs(interfaces) do
    if interface.up == true and interface.l3_device == chosen.dev then
      local found
      for _, item in ipairs(interface["ipv4-address"] or {}) do
        if ipv4(item.address) and (not chosen.prefsrc or chosen.prefsrc == item.address) then
          if found and found ~= item.address then return nil, "ambiguous default interface IPv4 addresses" end
          found = item.address
        end
      end
      if found then
        if selected then return nil, "multiple logical interfaces share default route device" end
        selected, address = interface, found
      end
    end
  end
  if not selected or type(selected.interface) ~= "string" or type(selected.proto) ~= "string" then
    return nil, "default route has no unique active netifd IPv4 interface"
  end
  return {route=chosen, interface=selected, address=address, device=chosen.dev}
end

local function metadata(uplink)
  return {device=uplink.device, measurement="interface-rx-tx", dnsReadOnly=true}
end

local function status_result(result, uplink, env)
  local config, err = env.config(uplink.interface.interface)
  if type(config) ~= "table" then return nil, err or "cannot read interface configuration" end
  local manual = uplink.interface.proto == "static" or tostring(config.peerdns) == "0"
  local dns = manual and config.dns or uplink.interface["dns-server"]
  if type(dns) == "string" then
    local entries = {}
    for entry in dns:gmatch("%S+") do entries[#entries+1] = entry end
    dns = entries
  end
  if dns ~= nil and type(dns) ~= "table" then return nil, "invalid interface DNS list" end
  result.defaultInterface = uplink.interface.interface
  result.proto = uplink.interface.proto
  result.ipv4addr = uplink.address
  result.ipv6addr = nil
  local addresses6 = uplink.interface["ipv6-address"] or {}
  if addresses6[1] and type(addresses6[1].address) == "string" then result.ipv6addr = addresses6[1].address end
  result.dnsProto = manual and "manual" or "auto"
  result.dnsList = copy(dns or {})
  local uptime = tonumber(uplink.interface.uptime)
  if not finite(uptime) or uptime < 0 then return nil, "invalid interface uptime" end
  result.uptimeStamp = uptime
  result.dashboardAdapter = metadata(uplink)
  return result
end

local function identity(uplink, env)
  local boot, boot_error = env.boot()
  local index, index_error = env.ifindex(uplink.device)
  if type(boot) ~= "string" or boot == "" then return nil, boot_error or "cannot read boot identity" end
  if not integer(index) or index == 0 then return nil, index_error or "cannot read device ifindex" end
  return table.concat({boot,tostring(index),uplink.device,uplink.address,
    uplink.route.gateway or "",uplink.interface.interface}, "|")
end

local function observation(uplink, env)
  local now, wall = env.monotonic(), env.epoch()
  local rx, tx = env.counter(uplink.device, "rx"), env.counter(uplink.device, "tx")
  if not finite(now) or now < 0 or not finite(wall) or wall <= 0 or not integer(rx) or not integer(tx) then
    return nil, "invalid monotonic time, timestamp or interface counters"
  end
  return {time=now, wall=wall, rx=rx, tx=tx}
end

local function usable_state(state, key, current)
  if type(state) ~= "table" or state.version ~= 1 or state.key ~= key or type(state.last) ~= "table" or type(state.items) ~= "table" then return false end
  local last = state.last
  if not finite(last.time) or not finite(last.wall) or not integer(last.rx) or not integer(last.tx) then return false end
  local dt = current.time - last.time
  if dt <= 0 or dt > MAX_INTERVAL or current.rx < last.rx or current.tx < last.tx then return false end
  if #state.items > MAX_ITEMS then return false end
  for _, item in ipairs(state.items) do
    if type(item) ~= "table" or not finite(item.startTime) or not finite(item.endTime) or item.endTime <= item.startTime or
      not finite(item.downloadSpeed) or item.downloadSpeed < 0 or not finite(item.uploadSpeed) or item.uploadSpeed < 0 then return false end
  end
  return true
end

local function sample(result, uplink, env)
  local release, lock_error = env.lock()
  if not release then return nil, lock_error or "dashboard sampler is busy" end
  local ok, value, err = pcall(function()
    local key, key_error = identity(uplink, env)
    if not key then return nil, key_error end
    local current, read_error = observation(uplink, env)
    if not current then return nil, read_error end
    local previous, load_error = env.load()
    if load_error then return nil, load_error end
    local last, items
    if usable_state(previous, key, current) then
      last, items = previous.last, copy(previous.items)
    else
      last, items = current, {}
      env.sleep(0.1)
      local after, select_error = select_uplink(env)
      if not after then return nil, select_error end
      local after_key, after_error = identity(after, env)
      if not after_key then return nil, after_error end
      if after_key ~= key then return nil, "uplink changed during measurement; retry" end
      current, read_error = observation(uplink, env)
      if not current then return nil, read_error end
    end
    local dt = current.time - last.time
    if dt <= 0 or dt > MAX_INTERVAL or current.rx < last.rx or current.tx < last.tx then
      return nil, "interface counters or monotonic clock changed during measurement; retry"
    end
    local confirmed, confirmation_error = select_uplink(env)
    if not confirmed then return nil, confirmation_error end
    local confirmed_key, confirmed_error = identity(confirmed, env)
    if not confirmed_key then return nil, confirmed_error end
    if confirmed_key ~= key then return nil, "uplink changed during measurement; retry" end
    -- Wall-clock timestamps anchor the chart; only monotonic time determines rates.
    items[#items+1] = {startTime=current.wall-dt, endTime=current.wall,
      downloadSpeed=(current.rx-last.rx)/dt, uploadSpeed=(current.tx-last.tx)/dt}
    while #items > MAX_ITEMS do table.remove(items, 1) end
    local stored, store_error = env.store({version=1,key=key,last=current,items=items})
    if not stored then return nil, store_error or "cannot atomically store dashboard samples" end
    result.items, result.slots, result.dashboardAdapter = items, MAX_ITEMS, metadata(uplink)
    return result
  end)
  local unlocked, unlock_error = pcall(release)
  if not unlocked then return nil, "cannot release sampler lock: " .. tostring(unlock_error) end
  if not ok then return nil, "dashboard measurement failed: " .. tostring(value) end
  return value, err
end

function M.transform(kind, body, env)
  if kind ~= "status" and kind ~= "statistics" then return nil, "unsupported adapter endpoint" end
  local value = env.decode(body)
  if type(value) ~= "table" then return nil, "upstream dashboard response is not JSON object" end
  -- Preserve legitimate backend errors byte-for-byte when transform is called with strings.
  if value.error ~= nil or (value.success ~= nil and value.success ~= true and value.success ~= 200 and value.success ~= 0) then return body end
  if type(value.result) ~= "table" then return nil, "upstream dashboard response has no result object" end
  local uplink, route_error = select_uplink(env)
  if not uplink then return nil, route_error end
  local adapted = copy(value)
  local result, err
  if kind == "status" then result,err = status_result(adapted.result,uplink,env)
  else result,err = sample(adapted.result,uplink,env) end
  if not result then return nil, err end
  return env.encode(adapted)
end

function M.production_env()
  local json = require "luci.jsonc"
  local util = require "luci.util"
  local nixio = require "nixio"
  local fs = require "nixio.fs"
  local uci = require("uci").cursor()
  local directory = "/tmp/s20p-dashboard-v1"
  local function read(path)
    local file = io.open(path, "r")
    if not file then return nil end
    local value = file:read("*a"); file:close(); return value
  end
  local function safe_device_path(device, suffix)
    if not device_name(device) then return nil end
    return "/sys/class/net/" .. device .. "/" .. suffix
  end
  local function private_directory()
    local stat = fs.lstat(directory)
    if not stat then
      if not fs.mkdir(directory, "700") then return nil, "cannot create private sample directory" end
      stat = fs.lstat(directory)
    end
    if not stat or stat.type ~= "dir" or stat.uid ~= 0 then return nil, "unsafe sample directory" end
    if not fs.chmod(directory, "700") then return nil, "cannot protect sample directory" end
    return true
  end
  local env = {decode=json.parse, encode=json.stringify}
  env.routes = function()
    local pipe = io.popen("ip -j -4 route show table main default 2>/dev/null", "r")
    if not pipe then return nil, "cannot run structured route query" end
    local output = pipe:read("*a"); pipe:close()
    local value = json.parse(output)
    if type(value) ~= "table" then return nil, "ip JSON route query failed (ip-full required)" end
    return value
  end
  env.interfaces = function()
    local value = util.ubus("network.interface", "dump", {})
    if type(value) ~= "table" or type(value.interface) ~= "table" then return nil, "netifd interface dump failed" end
    return value.interface
  end
  env.config = function(name) return uci:get_all("network", name) end
  env.boot = function() return (read("/proc/sys/kernel/random/boot_id") or ""):match("%S+") end
  env.ifindex = function(device) return tonumber(read(safe_device_path(device,"ifindex") or "")) end
  env.counter = function(device, kind)
    if kind ~= "rx" and kind ~= "tx" then return nil end
    return tonumber(read(safe_device_path(device,"statistics/" .. kind .. "_bytes") or ""))
  end
  env.monotonic = function() return tonumber((read("/proc/uptime") or ""):match("^[%d.]+")) end
  env.epoch = function()
    local seconds, useconds = nixio.gettimeofday()
    return seconds + (useconds or 0)/1000000
  end
  env.sleep = function() nixio.nanosleep(0, 100000000) end
  env.lock = function()
    local safe, err = private_directory()
    if not safe then return nil, err end
    local path = directory .. "/lock"
    local stat = fs.lstat(path)
    if stat and (stat.type ~= "reg" or stat.uid ~= 0) then return nil, "unsafe sampler lock" end
    local lock = nixio.open(path, nixio.open_flags("wronly", "creat"), "600")
    if not lock then return nil, "cannot open sampler lock" end
    if not lock:lock("tlock") then lock:close(); return nil, "dashboard sampler busy; retry" end
    return function() lock:lock("ulock"); lock:close() end
  end
  env.load = function()
    local path = directory .. "/state.json"
    local stat = fs.lstat(path)
    if not stat then return nil end
    if stat.type ~= "reg" or stat.uid ~= 0 or (stat.size or 0) > 65536 then return nil, "unsafe sampler state" end
    local contents = read(path)
    if not contents then return nil, "cannot read sampler state" end
    local value = json.parse(contents)
    -- A damaged optional history never supplies a rate: rebuild from a fresh
    -- measured pair. I/O failures above remain explicit failures.
    if type(value) ~= "table" then return nil end
    return value
  end
  env.store = function(value)
    local temporary = directory .. "/state.new"
    local contents = json.stringify(value)
    if type(contents) ~= "string" then return nil, "cannot encode sampler state" end
    local file = io.open(temporary, "w")
    if not file then return nil, "cannot open temporary sampler state" end
    local written = file:write(contents)
    local flushed = file:flush()
    local closed = file:close()
    if not written or not flushed or not closed or not fs.chmod(temporary,"600") or not fs.rename(temporary,directory .. "/state.json") then
      fs.unlink(temporary); return nil, "atomic sampler state update failed"
    end
    return true
  end
  return env
end

return M
